import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  type Document,
  type Health,
  type LLMTestResponse,
  type Profile,
} from "../api";

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [pinging, setPinging] = useState(false);
  const [pingResult, setPingResult] = useState<LLMTestResponse | null>(null);

  const [profile, setProfile] = useState<Profile | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [profileError, setProfileError] = useState<string | null>(null);

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch((e: Error) => setHealthError(e.message));
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const [p, docs] = await Promise.all([api.getProfile(), api.listDocuments()]);
        setProfile(p);
        setDocuments(docs);
      } catch (e) {
        setProfileError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  const onPing = async () => {
    setPinging(true);
    setPingResult(null);
    try {
      const result = await api.pingLLM();
      setPingResult(result);
    } catch (e) {
      setPingResult({
        ok: false,
        provider: health?.llm_provider ?? "?",
        model: health?.llm_model ?? "?",
        error: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setPinging(false);
    }
  };

  const resume = documents.find((d) => d.kind === "resume");
  const cover = documents.find((d) => d.kind === "cover_letter");
  const onboardingComplete = profile?.is_complete ?? false;

  return (
    <>
      <h1>Welcome to JobBee</h1>
      <p>
        The agent foundation is up. {onboardingComplete
          ? "Your profile is ready — the job search agent will plug in here next."
          : "Finish a quick onboarding so the agent knows what to search for."}
      </p>

      {profileError && (
        <div className="notice bad">Couldn't load your profile: {profileError}</div>
      )}

      {profile && !onboardingComplete && (
        <div className="card cta">
          <div>
            <h2>Set up your profile</h2>
            <p>
              Resume{resume ? " ✓" : " ·"} basics{profile.name ? " ✓" : " ·"} preferences
              {profile.target_roles.length ? " ✓" : ""}
            </p>
          </div>
          <Link to="/onboarding">
            <button>{profile.name ? "Resume onboarding" : "Start onboarding"}</button>
          </Link>
        </div>
      )}

      {profile && onboardingComplete && (
        <div className="card">
          <h2>Your profile</h2>
          <dl className="kv">
            <dt>Name</dt>
            <dd>{profile.name}</dd>
            {profile.headline && (
              <>
                <dt>Headline</dt>
                <dd>{profile.headline}</dd>
              </>
            )}
            {profile.location && (
              <>
                <dt>Location</dt>
                <dd>{profile.location}</dd>
              </>
            )}
            <dt>Target roles</dt>
            <dd>{profile.target_roles.join(", ") || <span className="muted">none</span>}</dd>
            <dt>Work modes</dt>
            <dd>{profile.work_modes.join(", ") || <span className="muted">none</span>}</dd>
            <dt>Resume</dt>
            <dd>
              {resume ? (
                <span className="badge ok">{resume.label}</span>
              ) : (
                <span className="badge bad">missing</span>
              )}
            </dd>
            <dt>Cover letter</dt>
            <dd>
              {cover ? (
                <span className="badge ok">{cover.label}</span>
              ) : (
                <span className="badge muted">none</span>
              )}
            </dd>
          </dl>
          <div className="button-row" style={{ marginTop: 12 }}>
            <Link to="/onboarding">
              <button className="secondary">Edit profile</button>
            </Link>
          </div>
        </div>
      )}

      <div className="row">
        <div className="card">
          <h2>Backend</h2>
          {healthError && (
            <div className="notice bad">
              Couldn't reach the backend: <span className="code">{healthError}</span>
              <br />
              Is <span className="code">uvicorn app.main:app --reload --port 8000</span>{" "}
              running?
            </div>
          )}
          {!health && !healthError && <div className="spinner">Checking…</div>}
          {health && (
            <dl className="kv">
              <dt>Status</dt>
              <dd>
                <span className="badge ok">{health.status}</span>
              </dd>
              <dt>Environment</dt>
              <dd>{health.app_env}</dd>
              <dt>Version</dt>
              <dd>{health.version}</dd>
              <dt>Database</dt>
              <dd>
                <span className={`badge ${health.db ? "ok" : "bad"}`}>
                  {health.db ? "connected" : "down"}
                </span>
              </dd>
            </dl>
          )}
        </div>

        <div className="card">
          <h2>LLM</h2>
          {health && (
            <dl className="kv">
              <dt>Provider</dt>
              <dd>{health.llm_provider}</dd>
              <dt>Model</dt>
              <dd>{health.llm_model}</dd>
            </dl>
          )}
          <div className="button-row">
            <button onClick={onPing} disabled={pinging || !health}>
              {pinging ? "Pinging…" : "Ping LLM"}
            </button>
            <Link to="/settings">
              <button className="secondary">Configure</button>
            </Link>
          </div>
          {pingResult && (
            <div className={`notice ${pingResult.ok ? "ok" : "bad"}`}>
              {pingResult.ok ? (
                <>
                  Reply from <span className="code">{pingResult.model}</span>:{" "}
                  <span className="code">{pingResult.content}</span>
                </>
              ) : (
                <>Error: {pingResult.error}</>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <h2>What's next</h2>
        <ol>
          <li>{onboardingComplete ? "✓" : "•"} Profile + resume / cover letter</li>
          <li>• Job search agent: Playwright + LinkedIn → matched job feed</li>
          <li>• Approvals: review matches, approve / reject</li>
          <li>• Apply agent: tailor docs, fill forms, stop at human-in-the-loop confirm</li>
          <li>• Tracker: status updates + notifications</li>
        </ol>
      </div>
    </>
  );
}
