import { useEffect, useRef, useState } from "react";
import {
  api,
  type Card,
  type JobInfo,
  type MatchCard as Match,
  type NoticeCard as Notice,
  type PreparingCard as Preparing,
  type RunCard,
  type SkipReason,
} from "../api";
import { minutesLeft, timeAgo } from "../lib/format";
import { AnswerList } from "./Answers";
import { StyleSwitch } from "./DocumentStyle";
import Icon from "./Icon";
import ScoreRing from "./ScoreRing";
import { errorText, useAction, useToast } from "./Toast";

type CardProps<T> = { card: T; onChanged: () => void };

const MODES: Record<string, string> = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" };

const SKIP_REASONS: { value: SkipReason; label: string }[] = [
  { value: "not_interested", label: "Not interested" },
  { value: "level", label: "Wrong level" },
  { value: "location", label: "Location" },
  { value: "salary", label: "Salary" },
  { value: "company", label: "Not this company" },
];

const HANDOFF_TITLES: Record<string, string> = {
  login: "Sign in to continue",
  account_required: "Create an account or sign in",
  captcha: "Solve the CAPTCHA",
  email_verification: "Enter the verification code",
  blocked_click: "Click this one yourself",
  other: "Your turn",
};

export function FeedCard({ card, onChanged }: CardProps<Card>) {
  switch (card.type) {
    case "match":
      return <MatchCard card={card} onChanged={onChanged} />;
    case "review":
      return <ReviewCard card={card} onChanged={onChanged} />;
    case "handoff":
      return <HandoffCard card={card} onChanged={onChanged} />;
    case "set_aside":
      return <SetAsideCard card={card} onChanged={onChanged} />;
    case "unconfirmed":
      return <UnconfirmedCard card={card} onChanged={onChanged} />;
    case "notice":
      return <NoticeCard card={card} onChanged={onChanged} />;
    case "preparing":
      return <PreparingCard card={card} />;
  }
}

export function cardKey(card: Card): string {
  if (card.type === "notice") return `notice-${card.id}`;
  if ("run_id" in card) return `${card.type}-${card.run_id}`;
  return `${card.type}-${card.application_id}`;
}

export function JobLine({ job, big = true }: { job: JobInfo; big?: boolean }) {
  const mode = job.work_mode ? MODES[job.work_mode] ?? job.work_mode : null;
  const repeated = mode && job.location?.toLowerCase().includes(mode.toLowerCase());
  const where = [job.location, repeated ? null : mode].filter(Boolean).join(" · ");
  return (
    <div className="grow">
      <div className={big ? "job-title" : "strong"}>{job.title}</div>
      <div className="job-sub">
        <span>
          <Icon name="building" />
          {job.company ?? "Unknown company"}
        </span>
        {where && (
          <span>
            <Icon name="pin" />
            {where}
          </span>
        )}
        {job.salary_text && (
          <span>
            <Icon name="money" />
            {job.salary_text}
          </span>
        )}
      </div>
    </div>
  );
}

// --- match (tap 1) --------------------------------------------------------------------

function MatchCard({ card, onChanged }: CardProps<Match>) {
  const { busy, run } = useAction();

  const apply = () =>
    void run(
      () => api.apply(card.application_id),
      "On it. You'll check the filled form before anything is submitted.",
    ).then((ok) => ok && onChanged());

  const skip = (reason?: SkipReason) =>
    void run(
      () => api.skip(card.application_id, reason),
      (r) => (r as { message: string }).message,
    ).then((ok) => ok && onChanged());

  const retailor = () =>
    void run(
      () => api.prepare(card.application_id),
      (r) => (r as { message: string }).message,
    ).then((ok) => ok && onChanged());

  return (
    <article className="feed-card">
      <div className="feed-card-head">
        <span className="badge accent">
          <Icon name="sparkle" />
          New match
        </span>
        <span className="faint">{timeAgo(card.prepared_at)}</span>
      </div>
      <div className="feed-card-body">
        <div className="row nowrap" style={{ alignItems: "flex-start", gap: 14 }}>
          <ScoreRing score={card.score} />
          <JobLine job={card.job} />
        </div>
        {card.summary && <p style={{ marginTop: 12 }}>{card.summary}</p>}
        {card.decision === "drop" && (
          <div className="notice warn small" style={{ marginTop: 10 }}>
            <Icon name="alert" />
            Probably not a fit, but you sent this one, so here it is.
          </div>
        )}
        {(card.reasons.length > 0 || card.gaps.length > 0) && (
          <div className="fit">
            {card.reasons.length > 0 && (
              <div>
                <div className="eyebrow">Why it fits</div>
                <ul>
                  {card.reasons.map((r) => (
                    <li key={r}>
                      <Icon name="check" className="icon good" />
                      {r}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {card.gaps.length > 0 && (
              <div>
                <div className="eyebrow">Gaps</div>
                <ul>
                  {card.gaps.map((g) => (
                    <li key={g}>
                      <Icon name="alert" className="icon gap" />
                      {g}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
        <div className="cv-box">
          <div className="row between">
            <div className="strong">
              <Icon name="file" /> Your CV for this job
            </div>
            <div className="row" style={{ gap: 6 }}>
              {card.resume_doc_id && (
                <a className="btn btn-sm" href={api.documentUrl(card.resume_doc_id)} target="_blank" rel="noreferrer">
                  View CV
                </a>
              )}
              {card.cover_doc_id && (
                <a className="btn btn-sm ghost" href={api.documentUrl(card.cover_doc_id)} target="_blank" rel="noreferrer">
                  Cover letter
                </a>
              )}
              {card.can_restyle && (
                <StyleSwitch applicationId={card.application_id} style={card.style} onChanged={onChanged} />
              )}
            </div>
          </div>
          {card.older_resume && (
            <div className="notice warn small" style={{ marginTop: 10, alignItems: "center" }}>
              <Icon name="alert" />
              <span className="grow">Made from your previous resume, before you replaced it.</span>
              <button className="sm" disabled={busy} onClick={retailor}>
                Update CV
              </button>
            </div>
          )}
          {card.changes.length > 0 && (
            <ul>
              {card.changes.slice(0, 4).map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          )}
          {card.removed_claims.length > 0 && (
            <div className="small muted" style={{ marginTop: 6 }}>
              Left out {card.removed_claims.length} claim{card.removed_claims.length > 1 ? "s" : ""}{" "}
              your resume doesn't back up.
            </div>
          )}
        </div>
      </div>
      <div className="feed-card-foot">
        <button className="primary" disabled={busy} onClick={apply}>
          <Icon name="send" />
          Apply
        </button>
        <SkipMenu disabled={busy} onSkip={skip} />
        <a className="btn ghost" href={card.job.url} target="_blank" rel="noreferrer">
          Posting <Icon name="external" />
        </a>
      </div>
    </article>
  );
}

function SkipMenu({ disabled, onSkip }: { disabled: boolean; onSkip: (r?: SkipReason) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div className="menu-wrap" ref={ref}>
      <button disabled={disabled} onClick={() => setOpen(!open)} aria-expanded={open}>
        Skip <Icon name="down" />
      </button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-title">Why? It helps pick better jobs.</div>
          {SKIP_REASONS.map((r) => (
            <button
              key={r.value}
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onSkip(r.value);
              }}
            >
              {r.label}
            </button>
          ))}
          <button
            role="menuitem"
            className="faint"
            onClick={() => {
              setOpen(false);
              onSkip();
            }}
          >
            Just skip
          </button>
        </div>
      )}
    </div>
  );
}

// --- review & submit (tap 2) ---------------------------------------------------------

function ReviewCard({ card, onChanged }: CardProps<RunCard>) {
  const { busy, run } = useAction();
  const toast = useToast();
  const [zoom, setZoom] = useState(false);
  const left = minutesLeft(card.expires_at);

  const decide = (submit: boolean) =>
    void run(
      () => api.decide(card.run_id, submit),
      submit ? undefined : "OK, not submitted.",
    ).then((ok) => ok && onChanged());

  const change = async (field: string, value: string) => {
    try {
      await api.revise(card.run_id, [{ field, value }]);
      toast("Changing it in the form. This takes a minute.");
      onChanged();
      return true;
    } catch (e) {
      toast(errorText(e), { bad: true });
      return false;
    }
  };

  return (
    <article className="feed-card attention">
      <div className="feed-card-head">
        {card.updating ? (
          <span className="badge info">
            <span className="spinner" />
            Changing your answers…
          </span>
        ) : (
          <span className="badge warn">
            <span className="dot pulse" />
            Ready to submit
          </span>
        )}
        {left != null && <span className="faint">form stays open {left} more min</span>}
      </div>
      <div className="feed-card-body">
        <JobLine job={card.job} />
        {card.summary && <p style={{ marginTop: 10 }}>{card.summary}</p>}
        {card.error && <div className="notice bad small" style={{ marginTop: 10 }}>{card.error}</div>}
        {card.checks.length > 0 && (
          <div className="checks">
            <div className="strong small">Please check</div>
            <ul>
              {card.checks.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
        )}
        {card.screenshot_url && (
          <img
            className="shot"
            style={{ marginTop: 12 }}
            src={card.screenshot_url}
            alt="The filled application form"
            onClick={() => setZoom(true)}
          />
        )}
        <AnswerList answers={card.answers} onChange={change} disabled={busy || card.updating} />
      </div>
      <div className="feed-card-foot">
        <button className="primary" disabled={busy || card.updating} onClick={() => decide(true)}>
          <Icon name="send" />
          {busy ? "Submitting…" : "Submit application"}
        </button>
        <button disabled={busy || card.updating} onClick={() => decide(false)}>
          Don't submit
        </button>
        <ShowBrowser runId={card.run_id} />
      </div>
      {zoom && card.screenshot_url && (
        <div className="lightbox" onClick={() => setZoom(false)}>
          <img src={card.screenshot_url} alt="The filled application form" />
        </div>
      )}
    </article>
  );
}

function ShowBrowser({ runId }: { runId: number }) {
  const { busy, run } = useAction();
  return (
    <button className="ghost" disabled={busy} onClick={() => void run(() => api.focusRun(runId))}>
      <Icon name="monitor" />
      Show browser
    </button>
  );
}

// --- only a person can do it ----------------------------------------------------------

/** Set aside: something only the user can do at their PC. The other applications carry on. */
function SetAsideCard({ card, onChanged }: CardProps<RunCard>) {
  const { busy, run } = useAction();
  return (
    <article className="feed-card">
      <div className="feed-card-head">
        <span className="badge">
          <Icon name="monitor" />
          For when you're at your PC
        </span>
        <span className="faint truncate">
          {card.job.company} · {card.job.title}
        </span>
      </div>
      <div className="feed-card-body">
        {card.message && <p>{card.message}</p>}
        <p className="small faint" style={{ marginTop: 8 }}>
          The other applications carry on without it. When you're at your PC, JobBee tries again
          and hands you the browser for this one step.
        </p>
      </div>
      <div className="feed-card-foot">
        <button
          className="primary"
          disabled={busy}
          onClick={() =>
            void run(() => api.attend(card.run_id), "Trying again now. Watch the JobBee Chrome window.").then(
              (ok) => ok && onChanged(),
            )
          }
        >
          <Icon name="monitor" />
          I'm at my PC, try now
        </button>
        <button
          className="ghost"
          disabled={busy}
          onClick={() => void run(() => api.dismissRun(card.run_id), "Skipped.").then((ok) => ok && onChanged())}
        >
          Skip this job
        </button>
      </div>
    </article>
  );
}

function HandoffCard({ card, onChanged }: CardProps<RunCard>) {
  const { busy, run } = useAction();
  const done = (note = "") =>
    void run(() => api.handoffDone(card.run_id, note)).then((ok) => ok && onChanged());

  return (
    <article className="feed-card attention">
      <div className="feed-card-head">
        <span className="badge warn">
          <Icon name="monitor" />
          Needs you at your PC
        </span>
        <span className="faint truncate">
          {card.job.company} · {card.job.title}
        </span>
      </div>
      <div className="feed-card-body">
        <h3>{HANDOFF_TITLES[card.handoff_kind ?? "other"] ?? "Your turn"}</h3>
        {card.message && <p className="muted" style={{ marginTop: 6 }}>{card.message}</p>}
        <p className="small faint" style={{ marginTop: 8 }}>
          Do it in the JobBee Chrome window, then press Done. JobBee carries on from there.
        </p>
      </div>
      <div className="feed-card-foot">
        <button className="primary" disabled={busy} onClick={() => done()}>
          <Icon name="check" />
          Done, continue
        </button>
        <ShowBrowser runId={card.run_id} />
        <button className="ghost" disabled={busy} onClick={() => done("The user could not do it right now.")}>
          I can't right now
        </button>
      </div>
    </article>
  );
}

function UnconfirmedCard({ card, onChanged }: CardProps<RunCard>) {
  const { busy, run } = useAction();
  const answer = (went: boolean) =>
    void run(
      () => api.outcome(card.run_id, went),
      went ? "Marked as applied." : "Marked as not submitted.",
    ).then((ok) => ok && onChanged());

  return (
    <article className="feed-card attention">
      <div className="feed-card-head">
        <span className="badge warn">
          <Icon name="question" />
          Did it go through?
        </span>
      </div>
      <div className="feed-card-body">
        <JobLine job={card.job} big={false} />
        <p className="muted" style={{ marginTop: 10 }}>
          {card.clicked
            ? "JobBee clicked Submit but didn't see a confirmation. Check the tab in the JobBee Chrome window."
            : `JobBee couldn't submit safely${card.message ? ` (${card.message})` : ""}. The form is still open in the JobBee Chrome window: submit it there if it looks right.`}
        </p>
      </div>
      <div className="feed-card-foot">
        <button className="primary" disabled={busy} onClick={() => answer(true)}>
          It went through
        </button>
        <button disabled={busy} onClick={() => answer(false)}>
          It didn't
        </button>
      </div>
    </article>
  );
}

// --- status cards --------------------------------------------------------------------

function NoticeCard({ card, onChanged }: CardProps<Notice>) {
  const { busy, run } = useAction();
  return (
    <div className={`notice ${card.level === "error" ? "bad" : "info"}`}>
      <Icon name={card.level === "error" ? "alert" : "info"} />
      <div className="grow">
        <div>{card.text}</div>
        <div className="row" style={{ marginTop: 8, gap: 6 }}>
          {card.retry && (
            <button
              className="sm"
              disabled={busy}
              onClick={() => void run(() => api.retryNotice(card.id), "Trying again…").then(onChanged)}
            >
              <Icon name="refresh" />
              Try again
            </button>
          )}
          <button
            className="sm ghost"
            disabled={busy}
            onClick={() => void run(() => api.dismissNotice(card.id)).then(onChanged)}
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}

function PreparingCard({ card }: { card: Preparing }) {
  return (
    <article className="feed-card">
      <div className="feed-card-body">
        <div className="row nowrap" style={{ gap: 14 }}>
          <div className="spinner" />
          <div className="grow">
            <div className="strong truncate">
              {card.job.title}
              {card.job.company ? ` · ${card.job.company}` : ""}
            </div>
            <div className="small muted">
              {card.stage === "tailoring" ? "Tailoring your CV…" : "Checking the fit…"}
            </div>
          </div>
        </div>
      </div>
    </article>
  );
}
