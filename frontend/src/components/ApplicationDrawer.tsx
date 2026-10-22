import { useCallback, useEffect, useState } from "react";
import { api, type ApplicationView, type ApplyEvent, type RunSummary } from "../api";
import { describeEvent, timeAgo, usd } from "../lib/format";
import { AnswerList, RunHighlights } from "./Answers";
import { JobLine } from "./cards";
import { StyleSwitch } from "./DocumentStyle";
import Icon from "./Icon";
import ScoreRing from "./ScoreRing";
import { errorText, useAction } from "./Toast";

const GROUP_BADGE: Record<string, string> = {
  applied: "ok",
  in_progress: "accent",
  skipped: "",
  not_a_fit: "",
  didnt_finish: "bad",
};

const RUN_LABELS: Record<string, string> = {
  queued: "Queued",
  starting: "Starting",
  running: "Filling the form",
  needs_user: "Waiting for your answer",
  needs_human: "Waiting for you at your PC",
  needs_you: "Set aside for when you're at your PC",
  awaiting_submit: "Waiting for your Submit",
  revising: "Changing your answers",
  submitting: "Submitting",
  submitted: "Submitted",
  submit_unconfirmed: "Not confirmed",
  discarded: "You chose not to submit",
  expired: "Form timed out",
  blocked: "Got stuck",
  failed: "Failed",
  cancelled: "Stopped",
};

/** Everything about one application: fit, CV, the posting, and what the agent did. */
export default function ApplicationDrawer({
  applicationId,
  onClose,
  onChanged,
}: {
  applicationId: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [view, setView] = useState<ApplicationView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { busy, run } = useAction();

  const load = useCallback(() => {
    api
      .getApplication(applicationId)
      .then(setView)
      .catch((e) => setError(errorText(e)));
  }, [applicationId]);

  useEffect(load, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const act = (fn: () => Promise<unknown>, message: string) =>
    void run(fn, message).then((ok) => {
      if (ok) {
        load();
        onChanged();
      }
    });

  const screen = view?.screen;
  const canPrepare = view && (view.status === "matched" || view.status === "filtered_out");

  return (
    <>
      <div className="overlay" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Application details">
        <div className="drawer-head">
          {view ? <JobLine job={view.job} big /> : <div className="grow skeleton" style={{ height: 40 }} />}
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="x" className="icon lg" />
          </button>
        </div>
        <div className="drawer-body">
          {error && <div className="notice bad">{error}</div>}
          {view && (
            <>
              <div className="row between">
                <span className={`badge ${GROUP_BADGE[view.group ?? "in_progress"]}`}>{view.label}</span>
                <span className="small faint">
                  {view.discovered_via ? `Found via ${view.discovered_via.replace(/_/g, " ")} · ` : ""}
                  {timeAgo(view.created_at)}
                </span>
              </div>
              {view.stage_reason && <div className="notice small">{view.stage_reason}</div>}

              <div className="row">
                <a className="btn btn-sm" href={view.job.url} target="_blank" rel="noreferrer">
                  Posting <Icon name="external" />
                </a>
                {view.status === "awaiting_approval" && (
                  <button className="sm primary" disabled={busy} onClick={() => act(() => api.apply(view.id), "On it.")}>
                    Apply
                  </button>
                )}
                {canPrepare && (
                  <button className="sm" disabled={busy} onClick={() => act(() => api.prepare(view.id), "Tailoring your CV…")}>
                    <Icon name="sparkle" />
                    {view.status === "filtered_out" ? "Prepare anyway" : "Prepare CV"}
                  </button>
                )}
                {view.can_retry && (
                  <button
                    className={view.runs.length ? "sm" : "sm primary"}
                    disabled={busy}
                    onClick={() => act(() => api.retry(view.id), view.runs.length ? "Queued it again." : "On it.")}
                  >
                    <Icon name={view.runs.length ? "refresh" : "send"} />
                    {view.runs.length ? "Fill the form again" : "Apply now"}
                  </button>
                )}
              </div>

              {screen && (
                <section className="card">
                  <div className="row nowrap" style={{ gap: 14, alignItems: "flex-start" }}>
                    <ScoreRing score={screen.score ?? null} size={46} />
                    <div className="grow">
                      <h3>Fit</h3>
                      {screen.summary && <p className="muted small">{screen.summary}</p>}
                    </div>
                  </div>
                  {(screen.reasons?.length || screen.gaps?.length) ? (
                    <ul className="plain-list" style={{ marginTop: 10 }}>
                      {screen.reasons?.map((r) => (
                        <li key={r}>
                          <Icon name="check" className="icon good" />
                          {r}
                        </li>
                      ))}
                      {screen.gaps?.map((g) => (
                        <li key={g}>
                          <Icon name="alert" className="icon gap" />
                          {g}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </section>
              )}

              {(view.resume_doc_id || view.changes.length > 0) && (
                <section className="card">
                  <div className="row between">
                    <h3>Your CV for this job</h3>
                    <div className="row" style={{ gap: 6 }}>
                      {view.resume_doc_id && (
                        <a className="btn btn-sm" href={api.documentUrl(view.resume_doc_id)} target="_blank" rel="noreferrer">
                          View CV
                        </a>
                      )}
                      {view.cover_doc_id && (
                        <a className="btn btn-sm ghost" href={api.documentUrl(view.cover_doc_id)} target="_blank" rel="noreferrer">
                          Cover letter
                        </a>
                      )}
                      {view.can_restyle && (
                        <StyleSwitch
                          applicationId={view.id}
                          style={view.style}
                          onChanged={() => {
                            load();
                            onChanged();
                          }}
                        />
                      )}
                    </div>
                  </div>
                  {view.changes.length > 0 && (
                    <ul className="small" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                      {view.changes.map((c) => (
                        <li key={c}>{c}</li>
                      ))}
                    </ul>
                  )}
                  {view.removed_claims.length > 0 && (
                    <details style={{ marginTop: 8 }}>
                      <summary className="small">Left out {view.removed_claims.length} unverified claim(s)</summary>
                      <ul className="small muted" style={{ paddingLeft: 18 }}>
                        {view.removed_claims.map((c) => (
                          <li key={c}>{c}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                </section>
              )}

              {view.runs.length > 0 && (
                <section className="card">
                  <h3>What JobBee did</h3>
                  <div className="stack" style={{ marginTop: 8 }}>
                    {view.runs.map((r, i) => (
                      <RunBlock key={r.id} run={r} defaultOpen={i === 0} />
                    ))}
                  </div>
                </section>
              )}

              {view.description && (
                <section className="card">
                  <details>
                    <summary>The job posting</summary>
                    <div className="jd" style={{ marginTop: 10 }}>
                      {view.description}
                    </div>
                  </details>
                </section>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );
}

function RunBlock({ run, defaultOpen }: { run: RunSummary; defaultOpen: boolean }) {
  const [events, setEvents] = useState<ApplyEvent[] | null>(null);
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    if (open && events == null) {
      api
        .getRunEvents(run.id)
        .then(setEvents)
        .catch(() => setEvents([]));
    }
  }, [open, events, run.id]);

  const lines = (events ?? [])
    .map((e) => ({ id: e.id, text: describeEvent(e) }))
    .filter((l) => l.text);

  return (
    <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        {RUN_LABELS[run.status] ?? run.status} · {timeAgo(run.created_at)}
        {run.attended && " · with you at your PC"}
        <span className="faint" style={{ fontWeight: 500 }}>
          {" "}
          · {run.iterations} steps · {usd(run.cost_usd)}
        </span>
      </summary>
      {run.summary && <p className="small" style={{ marginTop: 6 }}>{run.summary}</p>}
      {run.error && <div className="notice bad small" style={{ marginTop: 6 }}>{run.error}</div>}
      {events == null ? (
        <div className="skeleton" style={{ height: 60, marginTop: 8 }} />
      ) : (
        <>
          <RunHighlights events={events} answers={run.answers} />
          {run.checks.length > 0 && (
            <div className="checks">
              <div className="strong small">It asked you to check</div>
              <ul>
                {run.checks.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            </div>
          )}
          <AnswerList answers={run.answers} />
          {run.screenshot_url && (
            <div style={{ marginTop: 10 }}>
              <a className="small" href={run.screenshot_url} target="_blank" rel="noreferrer">
                The filled form, as you reviewed it <Icon name="external" />
              </a>
            </div>
          )}
          <details style={{ marginTop: 10 }}>
            <summary className="small">Every step ({lines.length})</summary>
            <div className="steps-log" style={{ marginTop: 8 }}>
              {lines.length === 0 ? <div>No steps recorded.</div> : lines.map((l) => <div key={l.id}>{l.text}</div>)}
            </div>
          </details>
        </>
      )}
    </details>
  );
}
