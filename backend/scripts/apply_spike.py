"""Phase 1 capability spike: Claude + the browser toolset fills real job applications.

Run from backend/ (your ANTHROPIC_API_KEY in backend/.env):

    .venv\\Scripts\\python.exe scripts\\apply_spike.py --url <job posting URL> [--url ...]
    .venv\\Scripts\\python.exe scripts\\apply_spike.py --job-id 42 --model claude-sonnet-5-5
    .venv\\Scripts\\python.exe scripts\\apply_spike.py --report

The same agent also runs from the UI (Apply page -> "Apply with Claude"); both write to
data/spike/scorecard.jsonl, which --report summarises.

For each URL it opens your own Chrome (a dedicated JobBee profile, headed) on the posting,
waits while you sign in to anything you need, then lets Claude fill the application using
your JobBee profile and base resume. Claude never submits: JobBee's driver blocks final-submit
clicks and Claude hands back at the review page. You check the browser, optionally submit
yourself, and answer three review questions. Each run writes data/spike/runs/<run>/ (transcript,
network HAR without bodies, summary).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.apply.accounts import credentials  # noqa: E402
from app.apply.agent import (  # noqa: E402
    EFFORTS,
    AgentConfig,
    PreflightError,
    guarded_driver,
    make_client,
    preflight,
    run_agent,
)
from app.apply.ats import detect_ats  # noqa: E402
from app.apply.browser import PROFILE_DIR, new_browser_session  # noqa: E402
from app.apply.costs import PRICES  # noqa: E402
from app.apply.dossier import UploadFile, build_dossier  # noqa: E402
from app.apply.inbox import wait_for_email  # noqa: E402
from app.apply.runs import build_task  # noqa: E402
from app.apply.scorecard import (  # noqa: E402
    SPIKE_DIR,
    append_scorecard,
    read_scorecard,
    submit_request_rows,
)
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import DocumentKind, Job, User  # noqa: E402
from app.services.documents import get_base_document  # noqa: E402

RUNS_DIR = SPIKE_DIR / "runs"


async def ainput(prompt: str) -> str:
    return (await asyncio.to_thread(input, prompt)).strip()


def say(text: str) -> None:
    print(text, flush=True)


# Page text echoed to the console can contain anything; never crash on a narrow code page.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def short(value: Any, limit: int = 140) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


class TerminalHooks:
    """Agent hooks for the console: print progress, hand over with input()."""

    def __init__(self, transcript: Path, user_id: int) -> None:
        self._file = transcript.open("a", encoding="utf-8")
        self._user_id = user_id

    def close(self) -> None:
        self._file.close()

    def event(self, kind: str, data: dict) -> None:
        row = {"t": datetime.now(timezone.utc).isoformat(), "kind": kind, **data}
        self._file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self._file.flush()
        if kind == "tool_call":
            say(f"   [{data['iteration']:>3}] {data['tool']} {short(data['input'])}")
        elif kind == "text":
            say(f"   [{data['iteration']:>3}] says: {data['text'].strip()[:300]}")
        elif kind == "guard_block":
            say(f"   [blocked] guard refused {data['action']} on {data['label']!r}")
        elif kind == "error":
            say(f"   [!] {data['message']}")

    async def request_human(self, kind: str, message: str) -> str:
        say(f"\n[you] Claude needs you ({kind}): {message}")
        return await ainput(
            "   Do it in the browser, then press Enter (or type why you couldn't): "
        )

    def site_login(self, host: str, action: str) -> tuple[str, str]:
        login, password, kind = credentials(self._user_id, host, action)
        self.event(kind, {"host": host, "login": login})
        return login, password

    async def find_email(self, host: str, purpose: str):
        since = datetime.now(timezone.utc) - timedelta(minutes=10)
        found = await wait_for_email(self._user_id, host, purpose, since)
        self.event("email_checked", {"host": host, "purpose": purpose, "found": bool(found)})
        return found


async def review_questions() -> dict:
    say("\nCheck the application in the browser, then answer:")
    reached = await ainput("  1. Did it reach the final review/submit step, form filled? [y/n] ")
    correct = await ainput("  2. Roughly what % of filled fields were correct? (0-100 or blank) ")
    submitted = await ainput("  3. Did the agent submit anything by itself? [y/n] ")
    notes = await ainput("  Notes (optional): ")
    return {
        "reached_review": reached.lower().startswith("y"),
        "field_accuracy_pct": int(correct) if correct.isdigit() else None,
        "agent_submitted": submitted.lower().startswith("y"),
        "notes": notes,
    }


async def run_one(
    client,
    *,
    url: str,
    user: User,
    resume_path: Path,
    resume_doc,
    cover_doc,
    job: Job | None,
    config: AgentConfig,
    profile_dir: Path,
) -> dict:
    ats = detect_ats(url)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"_{ats}"
    run_dir = RUNS_DIR / run_id
    uploads_dir = run_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    staged_resume = uploads_dir / resume_path.name
    shutil.copy2(resume_path, staged_resume)

    dossier = build_dossier(
        user,
        resume=resume_doc,
        cover_letter=cover_doc,
        job=job,
        uploads=[UploadFile("resume", staged_resume)],
    )
    hooks = TerminalHooks(run_dir / "transcript.jsonl", user.id)

    say(f"\n=== {run_id} | {ats} | {config.model} (effort {config.effort}) ===\n{url}")
    started = time.time()
    session = new_browser_session(profile_dir=profile_dir, har_path=run_dir / "network.har")
    await session.start()
    human: dict = {}
    observed: list[dict] = []
    try:
        await session.navigate_to(url)
        await ainput(
            "\nChrome is open on the posting. Sign in to anything you need there (LinkedIn, "
            "Workday...), then press Enter to start the agent: "
        )
        async with guarded_driver(session, uploads_dir, hooks) as driver:
            say("Agent running. Watch the browser; Ctrl+C aborts the run without scoring it.\n")
            stats = await run_agent(
                client,
                driver,
                task=build_task(url, dossier.render()),
                ats=ats,
                config=config,
                hooks=hooks,
            )
            mark = driver.network_mark()

            say("\n--- agent finished ---")
            if stats.finish:
                say(f"status: {stats.finish['status']}\n{stats.finish['summary']}")
                for item in stats.finish["fields_needing_review"]:
                    say(f"  - check: {item}")
            else:
                say(f"No finish() call ({stats.error}).")
            say(
                f"iterations {stats.iterations} | tool calls {stats.tool_calls} | "
                f"guard blocks {stats.guard_trips} | est. ${stats.cost_usd:.2f} | "
                f"{time.time() - started:.0f}s"
            )

            human = await review_questions()
            await ainput(
                "\nIf you want to submit this application, do it now in the browser (JobBee "
                "records the network request to learn this ATS's submit endpoint). "
                "Press Enter when done to close the browser: "
            )
            observed = driver.requests_since(mark)
    finally:
        await session.kill()
        hooks.close()

    candidates = submit_request_rows(observed)
    (run_dir / "submit_candidates.json").write_text(
        json.dumps(candidates, indent=2), encoding="utf-8"
    )
    row = {
        "run_id": run_id,
        "url": url,
        "ats": ats,
        "model": config.model,
        "effort": config.effort,
        "finish": stats.finish,
        "error": stats.error,
        "iterations": stats.iterations,
        "tool_calls": stats.tool_calls,
        "input_tokens": stats.input_tokens,
        "output_tokens": stats.output_tokens,
        "cache_read_tokens": stats.cache_read_tokens,
        "cache_write_tokens": stats.cache_write_tokens,
        "cost_usd": stats.cost_usd,
        "guard_trips": stats.guard_trips,
        "uninspected_clicks": stats.uninspected_clicks,
        "human_actions": [a["kind"] for a in stats.human_actions],
        "wall_s": round(time.time() - started),
        "submit_candidates": candidates[:5],
        **human,
    }
    (run_dir / "summary.json").write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
    append_scorecard(row)
    return row


def print_report() -> None:
    rows = read_scorecard()
    if not rows:
        say("No runs yet.")
        return
    header = f"{'run':<26} {'ats':<20} {'model':<18} {'status':<17} {'review':<6} {'acc%':>4} "
    header += f"{'submit!':<7} {'guard':>5} {'iters':>5} {'$':>6} {'secs':>5}"
    say(header)
    say("-" * len(header))
    for r in rows:
        status = (r.get("finish") or {}).get("status") or (r.get("error") or "-")[:16]
        reached = "yes" if r.get("reached_review") else "no"
        accuracy = r.get("field_accuracy_pct") or "-"
        agent_submitted = "YES" if r.get("agent_submitted") else "no"
        say(
            f"{r['run_id']:<26} {r['ats']:<20} {r['model']:<18} {status:<17} "
            f"{reached:<6} {accuracy:>4} {agent_submitted:<7} {r.get('guard_trips', 0):>5} "
            f"{r.get('iterations', 0):>5} {r.get('cost_usd', 0):>6.2f} {r.get('wall_s') or 0:>5}"
        )
    by_model: dict[str, list[dict]] = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)
    say("")
    for model, items in by_model.items():
        reached = sum(1 for r in items if r.get("reached_review"))
        cost = sum(r.get("cost_usd", 0) for r in items) / len(items)
        submits = sum(1 for r in items if r.get("agent_submitted"))
        say(
            f"{model}: reached review {reached}/{len(items)} | avg ${cost:.2f}/application | "
            f"agent submits {submits}"
        )


async def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", action="append", default=[], help="Job posting URL (repeatable).")
    parser.add_argument("--job-id", type=int, help="A JobBee job id; adds its description.")
    parser.add_argument("--model", default=settings.apply_model, choices=sorted(PRICES))
    parser.add_argument("--effort", default=settings.apply_effort, choices=EFFORTS)
    parser.add_argument("--max-iterations", type=int, default=settings.apply_max_iterations)
    parser.add_argument("--max-cost", type=float, default=settings.apply_max_cost_usd)
    parser.add_argument("--resume", type=Path, help="Resume file to upload (default: base resume).")
    parser.add_argument("--profile-dir", type=Path, default=PROFILE_DIR)
    parser.add_argument("--report", action="store_true", help="Print the scorecard and exit.")
    args = parser.parse_args()

    if args.report:
        print_report()
        return

    init_db()
    with SessionLocal() as session:
        user = session.execute(select(User).order_by(User.id)).scalars().first()
        if user is None:
            raise SystemExit("No JobBee profile yet. Complete onboarding first.")
        resume_doc = get_base_document(session, user.id, DocumentKind.resume)
        cover_doc = get_base_document(session, user.id, DocumentKind.cover_letter)
        job = session.get(Job, args.job_id) if args.job_id else None
        session.expunge_all()

    if resume_doc is None:
        raise SystemExit("Upload your resume in JobBee first (Onboarding).")
    resume_path = args.resume or (Path(resume_doc.file_path) if resume_doc.file_path else None)
    if resume_path is None or not resume_path.is_file():
        raise SystemExit("Your base resume has no file on disk; pass --resume path\\to\\resume.pdf")
    urls = list(args.url) or ([job.url] if job else [])
    if not urls:
        raise SystemExit("Pass --url (or --job-id).")

    config = AgentConfig(
        model=args.model,
        effort=args.effort,
        max_iterations=args.max_iterations,
        max_cost_usd=args.max_cost,
    )
    client = make_client()
    say(f"Preflight: checking {args.model} accepts the request shape...")
    try:
        await preflight(client, args.model, args.effort)
    except PreflightError as e:
        raise SystemExit(str(e)) from e
    say("Preflight OK.")

    for url in urls:
        await run_one(
            client,
            url=url,
            user=user,
            resume_path=resume_path,
            resume_doc=resume_doc,
            cover_doc=cover_doc,
            job=job if job and job.url == url else None,
            config=config,
            profile_dir=args.profile_dir,
        )
    say("")
    print_report()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        say("\nInterrupted.")
