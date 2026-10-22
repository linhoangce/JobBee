import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Preferences, type Profile, type SetupStatus } from "../api";
import ClaudeKey from "../components/ClaudeKey";
import Icon, { BeeMark } from "../components/Icon";
import PreferencesForm from "../components/PreferencesForm";
import ResumeImport from "../components/ResumeImport";
import { errorText } from "../components/Toast";

type Step = 0 | 1 | 2;

/** Three steps: connect Claude, drop your resume, say what you want. Then the agent starts. */
export default function Welcome({ setup, onDone }: { setup: SetupStatus; onDone: () => Promise<void> }) {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>(setup.claude_key ? 1 : 0);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    api.getProfile().then(setProfile).catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => {
    if (step === 2) {
      api
        .getPreferences()
        .then((p) =>
          setPrefs({
            ...p,
            work_modes: p.work_modes.length ? p.work_modes : ["remote", "hybrid"],
            target_locations:
              p.target_locations.length || !profile?.location ? p.target_locations : [profile.location],
          }),
        )
        .catch((e) => setError(errorText(e)));
    }
  }, [step, profile?.location]);

  const start = async () => {
    if (!prefs) return;
    setStarting(true);
    setError(null);
    try {
      await api.savePreferences(prefs);
      await api.finishSetup();
      await onDone();
      navigate("/", { replace: true });
    } catch (e) {
      setError(errorText(e));
      setStarting(false);
    }
  };

  const ready = prefs != null && prefs.target_roles.length > 0 && prefs.work_modes.length > 0;
  const roleSuggestions = profile?.headline ? [profile.headline.split(/[·|,–-]/)[0].trim()] : [];

  return (
    <div className="welcome">
      <div className="welcome-brand">
        <BeeMark />
        JobBee
      </div>
      <div className="progress" aria-label={`Step ${step + 1} of 3`}>
        {[0, 1, 2].map((i) => (
          <div key={i} className={i <= step ? "done" : ""} />
        ))}
      </div>

      {step === 0 && (
        <>
          <h1>Connect Claude</h1>
          <p className="lead">
            JobBee uses Claude to find jobs, check the fit, tailor your CV and fill in applications.
            You pay Anthropic directly for what it uses.
          </p>
          <ClaudeKey onStatus={(s) => s.connected && setStep(1)} />
        </>
      )}

      {step === 1 && (
        <>
          <h1>Your resume</h1>
          <p className="lead">
            Everything starts from it: which jobs fit, and the tailored CV for each one. Nothing is
            made up. Claims your resume doesn't support are left out.
          </p>
          {profile ? (
            <ResumeImport
              profile={profile}
              hasResume={setup.resume}
              onSaved={(p) => {
                setProfile(p);
                setStep(2);
              }}
            />
          ) : (
            <div className="skeleton" style={{ height: 170, borderRadius: 16 }} />
          )}
        </>
      )}

      {step === 2 && (
        <>
          <h1>What are you looking for?</h1>
          <p className="lead">
            Every day JobBee searches job boards, keeps the best matches and sends them to you with a
            tailored CV. You approve each one, and you see the filled form before it's submitted.
          </p>
          {prefs ? (
            <div className="card">
              <PreferencesForm
                value={prefs}
                onChange={setPrefs}
                roleSuggestions={roleSuggestions}
                locationSuggestions={profile?.location ? [profile.location] : []}
              />
            </div>
          ) : (
            <div className="skeleton" style={{ height: 320, borderRadius: 16 }} />
          )}
          <div className="row between" style={{ marginTop: 18 }}>
            <button className="ghost" onClick={() => setStep(1)}>
              Back
            </button>
            <button className="primary lg" disabled={!ready || starting} onClick={() => void start()}>
              {starting ? <span className="spinner" /> : <Icon name="search" />}
              Start finding jobs
            </button>
          </div>
          {!ready && prefs && (
            <p className="hint" style={{ textAlign: "right", marginTop: 6 }}>
              Add at least one role and one work mode.
            </p>
          )}
        </>
      )}

      {error && (
        <div className="notice bad" style={{ marginTop: 16 }}>
          <Icon name="alert" />
          {error}
        </div>
      )}

    </div>
  );
}
