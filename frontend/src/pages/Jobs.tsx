import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  type EmailStatus,
  type EmailSyncResponse,
  type Job,
  type Profile,
} from "../api";

function scoreBadge(score: number | null) {
  if (score == null) return <span className="badge muted">unscored</span>;
  if (score >= 75) return <span className="badge ok">{Math.round(score)}</span>;
  if (score >= 50) return <span className="badge">{Math.round(score)}</span>;
  return <span className="badge bad">{Math.round(score)}</span>;
}

export default function Jobs() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [syncingEmail, setSyncingEmail] = useState(false);
  const [emailStatus, setEmailStatus] = useState<EmailStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastEmailSync, setLastEmailSync] = useState<EmailSyncResponse | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const p = await api.getProfile();
        setProfile(p);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
      try {
        const j = await api.listJobs();
        setJobs(j);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        if (msg.includes("404")) {
          setError(
            "Jobs API not found — restart the backend (uvicorn) so it picks up the latest routes.",
          );
        } else {
          setError(msg);
        }
      }
      try {
        const status = await api.getEmailStatus();
        setEmailStatus(status);
      } catch {
        // Email API optional until backend is restarted.
      }
    })();
  }, []);

  const onSyncEmail = async () => {
    setSyncingEmail(true);
    setError(null);
    try {
      const result = await api.syncEmailAlerts({
        score: true,
        queue_for_approval: true,
      });
      setLastEmailSync(result);
      const refreshed = await api.listJobs();
      setJobs(refreshed);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncingEmail(false);
    }
  };

  const ready = profile && (profile.target_roles.length > 0 || profile.skills.length > 0);

  return (
    <>
      <h1>Job feed</h1>
      <p>
        JobBee ingests <strong>LinkedIn job-alert emails</strong> from your connected Gmail inbox,
        scores them against your profile, and queues top matches for approval. Automated LinkedIn
        scraping is intentionally disabled (ToS + ban risk).
      </p>

      {!ready && (
        <div className="card cta">
          <div>
            <h2>Profile needed</h2>
            <p>Add target roles or skills in onboarding so matches can be scored.</p>
          </div>
          <Link to="/onboarding">
            <button>Complete onboarding</button>
          </Link>
        </div>
      )}

      <div className="card">
        <h2>Sync job alerts</h2>
        <p className="muted small">
          Fetches new LinkedIn job-alert emails, parses listings, and scores up to 8 new jobs with
          AI per sync (the rest use fast keyword matching).
        </p>
        <div className="button-row">
          <button
            onClick={onSyncEmail}
            disabled={!ready || !emailStatus?.connected || syncingEmail}
            title={
              !ready
                ? "Complete onboarding first"
                : emailStatus?.connected
                  ? "Fetch LinkedIn job-alert emails and score matches"
                  : "Connect Gmail in Settings first"
            }
          >
            {syncingEmail ? "Syncing…" : "Sync from email"}
          </button>
        </div>
        {!emailStatus?.connected && (
          <p className="muted small">
            Connect Gmail in <Link to="/settings">Settings</Link> to start receiving job alerts.
          </p>
        )}
        {lastEmailSync && (
          <div className="notice ok" style={{ marginTop: 12 }}>
            Email sync · scanned {lastEmailSync.messages_scanned} · found {lastEmailSync.jobs_found}{" "}
            · new {lastEmailSync.jobs_upserted} · scored {lastEmailSync.jobs_scored}
            {lastEmailSync.approval_queued > 0 && (
              <>
                {" "}
                · queued {lastEmailSync.approval_queued} for approval
              </>
            )}
            {lastEmailSync.warnings.length > 0 && (
              <>
                <br />
                <span className="muted">{lastEmailSync.warnings.join(" · ")}</span>
              </>
            )}
          </div>
        )}
        {lastEmailSync && lastEmailSync.approval_queued > 0 && (
          <div className="notice ok" style={{ marginTop: 8 }}>
            <Link to="/approvals">
              Review {lastEmailSync.approval_queued} pending match(es) →
            </Link>
          </div>
        )}
      </div>

      {error && <div className="notice bad">{error}</div>}

      <div className="card">
        <h2>Matches ({jobs.length})</h2>
        {jobs.length === 0 && (
          <p className="muted">No jobs yet — connect Gmail and sync job alerts above.</p>
        )}
        <div className="job-list">
          {jobs.map((job) => (
            <article key={job.id} className="job-card">
              <div className="job-card-head">
                <div>
                  <div className="job-title">{job.title}</div>
                  <div className="muted small">
                    {job.company ?? "Unknown company"} · {job.location ?? "Remote"} ·{" "}
                    <span className="code">{job.source}</span>
                  </div>
                </div>
                {scoreBadge(job.match_score)}
              </div>
              {job.match_reason && (
                <p className="muted small job-match-reason">{job.match_reason}</p>
              )}
              {job.description && (
                <p className="job-desc">{job.description.replace(/<[^>]+>/g, "").slice(0, 280)}…</p>
              )}
              <div className="button-row">
                <a href={job.url} target="_blank" rel="noreferrer">
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
