import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Application, type ApprovalSummary } from "../api";

function scoreBadge(score: number | null) {
  if (score == null) return <span className="badge muted">unscored</span>;
  if (score >= 75) return <span className="badge ok">{Math.round(score)}</span>;
  if (score >= 50) return <span className="badge">{Math.round(score)}</span>;
  return <span className="badge bad">{Math.round(score)}</span>;
}

export default function Approvals() {
  const [topN, setTopN] = useState(5);
  const [summary, setSummary] = useState<ApprovalSummary | null>(null);
  const [items, setItems] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [queueing, setQueueing] = useState(false);
  const [actingOn, setActingOn] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      const [profile, approvals] = await Promise.all([api.getProfile(), api.listApprovals()]);
      setTopN(profile.approval_top_n ?? 5);
      setSummary(approvals.summary);
      setItems(approvals.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const onRebuildQueue = async () => {
    setQueueing(true);
    setError(null);
    try {
      await api.updateProfile({ approval_top_n: topN });
      const result = await api.queueApprovals({ top_n: topN });
      setSummary({
        top_n: result.top_n,
        awaiting_approval: result.items.length,
        approved: summary?.approved ?? 0,
        rejected: summary?.rejected ?? 0,
      });
      setItems(result.items);
      const refreshed = await api.listApprovals();
      setSummary(refreshed.summary);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setQueueing(false);
    }
  };

  const onApprove = async (id: number) => {
    setActingOn(id);
    setError(null);
    try {
      await api.approveApplication(id);
      setItems((prev) => prev.filter((item) => item.id !== id));
      const refreshed = await api.listApprovals();
      setSummary(refreshed.summary);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActingOn(null);
    }
  };

  const onReject = async (id: number) => {
    setActingOn(id);
    setError(null);
    try {
      await api.rejectApplication(id);
      setItems((prev) => prev.filter((item) => item.id !== id));
      const refreshed = await api.listApprovals();
      setSummary(refreshed.summary);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActingOn(null);
    }
  };

  return (
    <>
      <h1>Approvals</h1>
      <p>
        Review your top matches and approve the ones you want JobBee to pursue next. Rejected jobs
        stay out of the apply pipeline. Approved jobs move to{" "}
        <Link to="/tracker">Apply</Link>.
      </p>

      <div className="card">
        <h2>Queue settings</h2>
        <p className="muted small">
          After a job search, the highest-scored listings are promoted here (default: top 5). Change
          the count and rebuild to refresh from your latest matches.
        </p>
        <div className="field-row" style={{ maxWidth: 360 }}>
          <div className="field">
            <label htmlFor="top-n">Top matches to review</label>
            <input
              id="top-n"
              type="number"
              min={1}
              max={50}
              value={topN}
              onChange={(e) => setTopN(Number(e.target.value))}
            />
          </div>
        </div>
        <div className="button-row">
          <button onClick={onRebuildQueue} disabled={queueing || topN < 1 || topN > 50}>
            {queueing ? "Rebuilding…" : "Rebuild queue"}
          </button>
          <Link to="/jobs">
            <button className="secondary">Sync job alerts</button>
          </Link>
        </div>
        {summary && (
          <div className="approval-stats muted small" style={{ marginTop: 12 }}>
            Pending {summary.awaiting_approval} · Approved {summary.approved} · Rejected{" "}
            {summary.rejected}
          </div>
        )}
      </div>

      {error && <div className="notice bad">{error}</div>}

      <div className="card">
        <h2>Pending review ({items.length})</h2>
        {loading && <p className="muted">Loading…</p>}
        {!loading && items.length === 0 && (
          <p className="muted">
            Nothing waiting for approval.{" "}
            <Link to="/jobs">Run a job search</Link> or rebuild the queue above.
          </p>
        )}
        <div className="job-list">
          {items.map((item) => (
            <article key={item.id} className="job-card">
              <div className="job-card-head">
                <div>
                  <div className="job-title">{item.job.title}</div>
                  <div className="muted small">
                    {item.job.company ?? "Unknown company"} · {item.job.location ?? "Remote"} ·{" "}
                    <span className="code">{item.job.source}</span>
                  </div>
                </div>
                {scoreBadge(item.job.match_score)}
              </div>
              {item.job.match_reason && (
                <p className="muted small job-match-reason">{item.job.match_reason}</p>
              )}
              {item.job.description && (
                <p className="job-desc">
                  {item.job.description.replace(/<[^>]+>/g, "").slice(0, 280)}…
                </p>
              )}
              <div className="button-row">
                <button
                  className="approve-btn"
                  disabled={actingOn === item.id}
                  onClick={() => void onApprove(item.id)}
                >
                  Approve
                </button>
                <button
                  className="secondary reject-btn"
                  disabled={actingOn === item.id}
                  onClick={() => void onReject(item.id)}
                >
                  Reject
                </button>
                <a href={item.job.url} target="_blank" rel="noreferrer">
                  <button className="secondary">View posting</button>
                </a>
              </div>
            </article>
          ))}
        </div>
      </div>
    </>
  );
}
