import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApplicationDetail, type ApplicationSummary } from "../api";

type ActionKind = "tailor" | "prepare" | "confirm" | "fail";

function statusBadge(status: ApplicationDetail["status"]) {
  if (status === "approved") return <span className="badge ok">approved</span>;
  if (status === "submitted") return <span className="badge">submitted</span>;
  if (status === "failed") return <span className="badge bad">failed</span>;
  return <span className="badge muted">{status}</span>;
}

export default function Tracker() {
  const [summary, setSummary] = useState<ApplicationSummary | null>(null);
  const [items, setItems] = useState<ApplicationDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [actingOn, setActingOn] = useState<number | null>(null);
  const [actionKind, setActionKind] = useState<ActionKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const load = async () => {
    setError(null);
    try {
      const resp = await api.listApplications();
      setSummary(resp.summary);
      setItems(resp.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const runAction = async (id: number, kind: ActionKind) => {
    setActingOn(id);
    setActionKind(kind);
    setError(null);
    setWarnings([]);
    try {
      if (kind === "tailor") {
        const result = await api.tailorApplication(id);
        setWarnings(result.warnings);
        setExpandedId(id);
      } else if (kind === "prepare") {
        const result = await api.prepareApplication(id);
        setWarnings(result.warnings);
        setExpandedId(id);
      } else if (kind === "confirm") {
        const result = await api.confirmSubmit(id);
        setWarnings(result.warnings);
      } else {
        await api.markApplicationFailed(id, "Marked failed from tracker");
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActingOn(null);
      setActionKind(null);
    }
  };

  const actionLabel = (id: number, kind: ActionKind, idle: string) => {
    if (actingOn === id && actionKind === kind) {
      if (kind === "tailor") return "Tailoring…";
      if (kind === "prepare") return "Preparing…";
      if (kind === "confirm") return "Confirming…";
      return "Updating…";
    }
    return idle;
  };

  return (
    <>
      <h1>Apply pipeline</h1>
      <p>
        Approved jobs move here. JobBee tailors your documents, opens the posting (LinkedIn Easy
        Apply or external Apply), fills forms from your profile, and stops before the final submit
        — you confirm when ready.
      </p>

      {summary && (
        <div className="approval-stats muted small" style={{ marginBottom: 16 }}>
          Approved {summary.approved} · Submitted {summary.submitted} · Failed {summary.failed}
        </div>
      )}

      {error && <div className="notice bad">{error}</div>}
      {warnings.length > 0 && (
        <div className="notice" style={{ marginBottom: 16 }}>
          {warnings.map((w) => (
            <div key={w}>{w}</div>
          ))}
        </div>
      )}

      <div className="card">
        <h2>Applications</h2>
        {loading && <p className="muted">Loading…</p>}
        {!loading && items.length === 0 && (
          <p className="muted">
            No approved applications yet.{" "}
            <Link to="/approvals">Approve jobs</Link> to start the apply pipeline.
          </p>
        )}
        <div className="job-list">
          {items.map((item) => {
            const hasDocs = item.resume_doc_id != null && item.cover_doc_id != null;
            const expanded = expandedId === item.id;
            const meta = item.apply_meta;
            const fieldsFilled = Array.isArray(meta?.fields_filled)
              ? (meta.fields_filled as string[])
              : [];
            const browserEngine =
              typeof meta?.browser_engine === "string" ? meta.browser_engine : null;
            const screenshotPath =
              typeof meta?.screenshot_path === "string" ? meta.screenshot_path : null;
            const browserUseLiveUrl =
              typeof meta?.browser_use_live_url === "string" ? meta.browser_use_live_url : null;
            return (
              <article key={item.id} className="job-card">
                <div className="job-card-head">
                  <div>
                    <div className="job-title">{item.job.title}</div>
                    <div className="muted small">
                      {item.job.company ?? "Unknown company"} · {statusBadge(item.status)}
                      {item.submitted_at && (
                        <> · submitted {new Date(item.submitted_at).toLocaleString()}</>
                      )}
                    </div>
                  </div>
                </div>

                {item.apply_prepared && meta && (
                  <p className="muted small">
                    Form prepared · fields: {fieldsFilled.join(", ") || "none"}
                    {browserEngine === "browser_use" && " · Browser Use"}
                  </p>
                )}

                <div className="button-row">
                  {item.status === "approved" && (
                    <>
                      <button
                        disabled={actingOn === item.id}
                        onClick={() => void runAction(item.id, "tailor")}
                      >
                        {actionLabel(item.id, "tailor", hasDocs ? "Re-tailor docs" : "Tailor docs")}
                      </button>
                      <button
                        className="secondary"
                        disabled={actingOn === item.id || !hasDocs}
                        onClick={() => void runAction(item.id, "prepare")}
                      >
                        {actionLabel(item.id, "prepare", "Prepare form")}
                      </button>
                      {actingOn === item.id && actionKind === "prepare" && (
                        <p className="muted small" style={{ marginTop: 8 }}>
                          Browser open — JobBee detects Easy Apply or external Apply, fills forms,
                          and steps through pages. Complete any remaining steps, then{" "}
                          <strong>close the browser window</strong> to finish.
                        </p>
                      )}
                      <button
                        className="approve-btn"
                        disabled={actingOn === item.id}
                        onClick={() => void runAction(item.id, "confirm")}
                      >
                        {actionLabel(item.id, "confirm", "Confirm submit")}
                      </button>
                    </>
                  )}
                  {item.status === "approved" && (
                    <button
                      className="secondary reject-btn"
                      disabled={actingOn === item.id}
                      onClick={() => void runAction(item.id, "fail")}
                    >
                      {actionLabel(item.id, "fail", "Mark failed")}
                    </button>
                  )}
                  <a href={item.job.url} target="_blank" rel="noreferrer">
                    <button className="secondary">View posting</button>
                  </a>
                  {(hasDocs || item.apply_prepared) && (
                    <button className="secondary" onClick={() => setExpandedId(expanded ? null : item.id)}>
                      {expanded ? "Hide details" : "Show details"}
                    </button>
                  )}
                </div>

                {expanded && (
                  <div className="tracker-details">
                    {item.resume_preview && (
                      <div>
                        <h3 className="small-heading">Tailored resume (preview)</h3>
                        <pre className="doc-preview">{item.resume_preview}</pre>
                      </div>
                    )}
                    {item.cover_preview && (
                      <div>
                        <h3 className="small-heading">Cover letter (preview)</h3>
                        <pre className="doc-preview">{item.cover_preview}</pre>
                      </div>
                    )}
                    {item.apply_prepared && screenshotPath && (
                      <div className="button-row">
                        <a href={api.applicationScreenshotUrl(item.id)} target="_blank" rel="noreferrer">
                          <button className="secondary">Open screenshot</button>
                        </a>
                      </div>
                    )}
                    {item.apply_prepared && browserUseLiveUrl && (
                      <div className="button-row">
                        <a
                          href={browserUseLiveUrl}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <button className="secondary">Open live browser</button>
                        </a>
                      </div>
                    )}
                    {item.status === "approved" && item.apply_prepared && (
                      <p className="muted small">
                        A browser window may open to fill the form. Review it, then click{" "}
                        <strong>Confirm submit</strong> only after you are ready — JobBee will not
                        submit without your explicit confirmation.
                      </p>
                    )}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      </div>
    </>
  );
}
