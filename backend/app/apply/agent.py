"""The Claude apply-agent loop, shared by the spike CLI and the background run manager.

Callers own the browser session and supply `AgentHooks` for what only they can do:
record events, hand the browser to a human (if one is there), look up the
applicant's site account, and read a site's email from the applicant's inbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from anthropic import APIStatusError, AsyncAnthropic, beta_async_tool
from anthropic.tools import ToolError
from anthropic.tools.browser import BetaConfirmContext, BetaLocalFilePolicy
from pydantic import BaseModel, Field

from ..llm.usage import check_budget, record_usage
from ..services.secrets import claude_api_key, claude_headers
from .costs import FALLBACK_MODELS
from .guards import url_refusal_reason
from .inbox import PURPOSES, InboxFind, InboxUnavailable
from .prompts import system_prompt
from .spike_driver import GuardedBrowserUse

MAX_TOKENS = 16_000
EFFORTS = ("low", "medium", "high", "xhigh", "max")
HUMAN_ACTION_KINDS = (
    "login",
    "account_required",
    "captcha",
    "email_verification",
    "blocked_click",
    "other",
)


ANSWER_SOURCES = (
    "profile",
    "resume",
    "standard_answer",
    "saved_answer",
    "judgement",
    "consent",
)
NOBODY_THERE = (
    "Nobody is at the computer: this run works unattended. Call `finish` with status "
    "'needs_you' and say exactly what the human must do at their PC (e.g. 'solve the CAPTCHA "
    "on the sign-up page'). JobBee sets this application aside and tells them."
)


class FilledField(BaseModel):
    field: str = Field(description="The form's label for the field or question.")
    value: str = Field(description="What you entered or selected (file name for uploads).")
    source: Literal[
        "profile", "resume", "standard_answer", "saved_answer", "judgement", "consent"
    ] = Field(
        default="profile",
        description=(
            "Where the answer came from. 'judgement': your own best answer where the dossier "
            "had none. 'consent': terms, privacy statements, attestations and signatures."
        ),
    )
    note: str | None = Field(
        default=None,
        description="For 'judgement': why, in one line. For 'consent': what you agreed to.",
    )


class AgentHooks(Protocol):
    def event(self, kind: str, data: dict[str, Any]) -> None: ...

    async def request_human(self, kind: str, message: str) -> str | None:
        """Empty when the human finished, else why they couldn't; None if nobody is there."""
        ...

    def site_login(self, host: str, action: str) -> tuple[str, str]:
        """(login, password) of the applicant's account on `host`; raises ToolError if not.
        For "rejected" it records the refusal and the password is empty."""
        ...

    async def find_email(self, host: str, purpose: str) -> InboxFind | None:
        """The site's email for `purpose`; None if none came; raises InboxUnavailable."""
        ...


@dataclass
class AgentConfig:
    model: str
    effort: str
    max_iterations: int
    max_cost_usd: float


@dataclass
class RunStats:
    iterations: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    stop_reason: str | None = None
    finish: dict | None = None
    error: str | None = None
    guard_trips: int = 0
    uninspected_clicks: int = 0
    human_actions: list[dict] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        if self.finish:
            return self.finish["status"]
        return "failed"


def make_client() -> AsyncAnthropic:
    return AsyncAnthropic(
        api_key=claude_api_key() or None,
        default_headers=claude_headers() or None,
        base_url="https://api.anthropic.com",  # never a proxy from ANTHROPIC_BASE_URL
    )


def request_params(model: str, effort: str, system: str) -> dict:
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": system}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        # Attended runs can pause for the human, so keep the prefix cached for an hour.
        # No context editing: past its trigger it clears on every request, so each step
        # rewrote the whole cached conversation. A full application fits the 1M window, and
        # reading it back from the cache is cheap.
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }
    if model in FALLBACK_MODELS:
        params["fallbacks"] = "default"
        params["betas"] = ["server-side-fallback-2026-07-01"]
    return params


class PreflightError(RuntimeError):
    pass


def api_error_message(error: APIStatusError) -> str:
    """The API's own explanation (e.g. "Your credit balance is too low…"), not the raw dump."""

    body = error.body if isinstance(error.body, dict) else {}
    detail = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
    return detail.get("message") or error.message


async def preflight(client: AsyncAnthropic, model: str, effort: str) -> None:
    """One tiny request with the real parameters, so config errors show before the browser opens."""

    check_budget()
    params = request_params(model, effort, "Reply with the single word OK.")
    params["max_tokens"] = 1024
    try:
        response = await client.beta.messages.create(
            **params,
            tools=[{"type": "browser_toolset_20260801"}],
            messages=[{"role": "user", "content": "Reply with the single word OK."}],
        )
    except APIStatusError as e:
        raise PreflightError(f"Claude ({model}) refused the request: {api_error_message(e)}") from e
    record_usage(model, response.usage, task="apply")


def guarded_driver(session, uploads_dir: Path, hooks: AgentHooks) -> GuardedBrowserUse:
    """The browser toolset driver with JobBee's submit guard, URL policy and upload confinement."""

    def url_policy(context, url: str) -> None:
        reason = url_refusal_reason(url)
        if reason:
            hooks.event("url_refused", {"url": url, "reason": reason})
            raise ToolError(f"Navigation refused: {reason}.")

    async def confirm(context: BetaConfirmContext) -> bool:
        if context.member == "file_upload":
            hooks.event("file_upload", {"input": context.input.model_dump()})
        return True  # the file policy already confines uploads to this run's staging dir

    return GuardedBrowserUse(
        session,
        on_event=hooks.event,
        configs={"file_upload": {"enabled": True}},
        confirm=confirm,
        url_policy=url_policy,
        file_policy=BetaLocalFilePolicy(upload_roots=[uploads_dir]),
    )


def make_tools(stats: RunStats, hooks: AgentHooks, driver: GuardedBrowserUse) -> list:
    @beta_async_tool
    async def site_account(
        action: Literal["sign_in", "create", "set_new_password", "rejected"],
        password_refs: list[str] | None = None,
        email_ref: str | None = None,
    ) -> str:
        """Fill the applicant's email and password on this site's sign-in, create-account or
        new-password form. JobBee types them; you never see or type a password. Then click the
        form's button yourself.

        Args:
            action: "sign_in" with the saved account; "create" for a new account (JobBee makes
                and saves a strong password); "set_new_password" on a password-reset form;
                "rejected" right after the site refused what JobBee filled (wrong password,
                locked account, email already registered), so JobBee never tries it again.
            password_refs: Refs of the password inputs: one, or two when the form asks to
                confirm or verify it. Not needed for "rejected".
            email_ref: Ref of the email or username input, if the form has one.
        """
        if action == "rejected":
            hooks.site_login(await driver.tab_host(), action)
            return (
                "Noted: JobBee won't use that password again. Use the site's 'Forgot password' "
                "link, then `check_inbox` (purpose 'reset_password'), then `site_account` "
                "(action 'set_new_password'), then sign in."
            )
        if not password_refs:
            raise ToolError("Pass the ref of the password field.")
        host = await driver.ref_host(password_refs[0])
        login, password = hooks.site_login(host, action)
        await driver.fill_credentials(
            host, login_ref=email_ref, login=login, password_refs=password_refs, password=password
        )
        what = {
            "sign_in": "sign-in",
            "create": "a new account",
            "set_new_password": "a new password",
        }
        return (
            f"Filled {what[action]} for {login} on {host}. Now click the form's button, then "
            "re-read the page. If the site says it sent an email, call `check_inbox`. If it "
            "refuses (wrong password, locked account, email already registered), call "
            "`site_account` with action 'rejected' and don't try the same thing again."
        )

    @beta_async_tool
    async def check_inbox(
        purpose: Literal["verify_email", "reset_password", "sign_in_code"],
    ) -> str:
        """Get the email this site just sent the applicant, from their connected inbox: a link is
        opened in your tab for you, a code is returned for you to type. Waits up to 3 minutes,
        so call it only after the site says it sent the email.

        Args:
            purpose: "verify_email" (confirm a new account), "reset_password" (a reset link),
                or "sign_in_code" (a one-time code to type).
        """
        if purpose not in PURPOSES:
            raise ToolError(f"Unknown purpose {purpose!r}.")
        host = await driver.tab_host()
        try:
            found = await hooks.find_email(host, purpose)
        except InboxUnavailable as e:
            return (
                f"{e} Call `request_human_action` (kind 'email_verification'). If nobody is "
                "there, tell the applicant in `finish` to connect Gmail in JobBee's Settings → "
                "Where to look, so JobBee can read this email itself next time."
            )
        if found is None:
            return (
                "No email from this site arrived in 3 minutes. Look for a 'resend' link and try "
                "once more; otherwise call `request_human_action` (kind 'email_verification')."
            )
        if found.code:
            return f"Code from {found.sender}: {found.code}. Type it into the code field."
        reason = url_refusal_reason(found.link or "")
        if reason:
            return f"The email's link can't be opened ({reason})."
        await driver.open_url(found.link)
        return f"Opened the link from {found.sender} in your tab. Re-read the page."

    @beta_async_tool
    async def request_human_action(
        kind: Literal[
            "login", "account_required", "captcha", "email_verification", "blocked_click", "other"
        ],
        message: str,
    ) -> str:
        """Hand the browser to the human for a step only they can do: signing in or creating an
        account, a CAPTCHA, an email/SMS code, or clicking a control JobBee blocked that is really
        an intermediate step. Returns when they are done; re-read the page afterwards.

        Args:
            kind: What the human needs to do.
            message: Exactly what you need from them, e.g. which button to click and why.
        """
        reply = await hooks.request_human(kind, message)
        if reply is None:
            return NOBODY_THERE
        hooks.event("human_action", {"kind": kind, "message": message})
        reply = reply.strip()
        stats.human_actions.append({"kind": kind, "message": message, "reply": reply})
        hooks.event("human_done", {"kind": kind, "reply": reply})
        if reply:
            return f"The human could not complete it: {reply}. Continue if you can, else finish."
        return "The human is done. Re-read the page (read_page or screenshot) before continuing."

    @beta_async_tool
    async def finish(
        status: Literal["ready_for_review", "needs_you", "blocked"],
        summary: str,
        answers: list[FilledField] | None = None,
        fields_needing_review: list[str] | None = None,
    ) -> str:
        """End this run. The applicant reviews it on their phone.

        Args:
            status: "ready_for_review" when the application is filled and waiting on the final
                review/submit page; "needs_you" when only a human at the PC can go on (after
                `request_human_action` said nobody is there); "blocked" when it can't be done.
            summary: What you did and where things stand, in two or three sentences. For
                "needs_you", exactly what the human must do.
            answers: Every field you filled or selected, across all pages, in order, with its
                source, so the applicant can check them without scrolling the form.
            fields_needing_review: Anything else the applicant should double-check.
        """
        stats.finish = {
            "status": status,
            "summary": summary,
            "answers": [
                a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in answers or []
            ],
            "fields_needing_review": fields_needing_review or [],
        }
        hooks.event("finish", stats.finish)
        return "Recorded. The human takes over now; don't call any more tools."

    return [site_account, check_inbox, request_human_action, finish]


async def run_agent(
    client: AsyncAnthropic,
    driver: GuardedBrowserUse,
    *,
    task: str,
    ats: str,
    config: AgentConfig,
    hooks: AgentHooks,
) -> RunStats:
    """Drive one application until Claude calls `finish`, gives up, or a cap is hit."""

    stats = RunStats()
    runner = client.beta.messages.tool_runner(
        **request_params(config.model, config.effort, system_prompt(ats)),
        max_iterations=config.max_iterations,
        tools=[driver, *make_tools(stats, hooks, driver)],
        messages=[{"role": "user", "content": task}],
    )
    try:
        async for message in runner:
            stats.iterations += 1
            usage = message.usage
            stats.input_tokens += usage.input_tokens or 0
            stats.output_tokens += usage.output_tokens or 0
            stats.cache_read_tokens += usage.cache_read_input_tokens or 0
            stats.cache_write_tokens += usage.cache_creation_input_tokens or 0
            stats.cost_usd += record_usage(config.model, usage, task="apply")
            stats.stop_reason = message.stop_reason
            for block in message.content:
                if block.type == "tool_use":
                    stats.tool_calls += 1
                    toolset = getattr(block, "toolset_name", None) or "tool"
                    hooks.event(
                        "tool_call",
                        {
                            "iteration": stats.iterations,
                            "tool": f"{toolset}.{block.name}",
                            "input": block.input,
                        },
                    )
                elif block.type == "text" and block.text.strip():
                    hooks.event("text", {"iteration": stats.iterations, "text": block.text})
            hooks.event(
                "usage",
                {
                    "iteration": stats.iterations,
                    "stop_reason": message.stop_reason,
                    "usage": usage.model_dump(),
                    "cost_usd": round(stats.cost_usd, 4),
                },
            )
            if message.stop_reason == "refusal":
                stats.error = f"Claude declined: {getattr(message, 'stop_details', None)}"
                break
            if stats.cost_usd > config.max_cost_usd:
                stats.error = f"Stopped: estimated cost passed ${config.max_cost_usd:.2f}"
                break
    except APIStatusError as e:
        stats.error = f"Claude API error {e.status_code}: {api_error_message(e)}"
    if stats.error:
        hooks.event("error", {"message": stats.error})
    if stats.finish is None and stats.error is None:
        stats.error = f"Claude stopped without finishing ({stats.stop_reason})."
    stats.guard_trips = driver.guard_trips
    stats.uninspected_clicks = driver.uninspected_clicks
    return stats
