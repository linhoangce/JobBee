import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  api,
  type EmailStatus,
  type EmailSyncResponse,
  type LLMTestResponse,
  type Settings as SettingsT,
} from "../api";
import GmailConnect from "../components/GmailConnect";

function decodeHtml(text: string): string {
  const el = document.createElement("textarea");
  el.innerHTML = text;
  return el.value;
}

type Provider = "openai" | "ollama";

const PROVIDER_PRESETS: Record<Provider, { base_url: string; model: string }> = {
  openai: {
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    model: "gemma-4-26b-a4b-it",
  },
  ollama: { base_url: "http://localhost:11434", model: "gemma4:e4b" },
};

export default function Settings() {
  const [loaded, setLoaded] = useState(false);
  const [provider, setProvider] = useState<Provider>("openai");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [hasStoredKey, setHasStoredKey] = useState(false);
  const [temperature, setTemperature] = useState(0.2);
  const [timeoutS, setTimeoutS] = useState(60);
  const [applyVisionEnabled, setApplyVisionEnabled] = useState(false);
  const [applyVisionModel, setApplyVisionModel] = useState("");

  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [saveOk, setSaveOk] = useState(true);

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<LLMTestResponse | null>(null);

  const [emailStatus, setEmailStatus] = useState<EmailStatus | null>(null);
  const [emailMsg, setEmailMsg] = useState<string | null>(null);
  const [emailOk, setEmailOk] = useState(true);
  const [emailBusy, setEmailBusy] = useState(false);
  const [syncResult, setSyncResult] = useState<EmailSyncResponse | null>(null);

  const [searchParams, setSearchParams] = useSearchParams();

  const applyServerState = (s: SettingsT) => {
    setProvider(s.llm_provider);
    setBaseUrl(s.llm_base_url);
    setModel(s.llm_model);
    setTemperature(s.llm_temperature);
    setTimeoutS(s.llm_timeout_s);
    setApplyVisionEnabled(s.apply_vision_enabled);
    setApplyVisionModel(s.apply_vision_model);
    setHasStoredKey(s.has_api_key);
    setLoaded(true);
  };

  useEffect(() => {
    api
      .getSettings()
      .then(applyServerState)
      .catch((e: Error) => {
        setSaveMsg(`Couldn't load settings: ${e.message}`);
        setSaveOk(false);
      });
  }, []);

  const refreshEmailStatus = () =>
    api.getEmailStatus().then(setEmailStatus).catch((e: Error) => {
      setEmailOk(false);
      setEmailMsg(e.message);
    });

  useEffect(() => {
    refreshEmailStatus();
  }, []);

  useEffect(() => {
    const connected = searchParams.get("email_connected");
    const error = searchParams.get("email_error");
    if (connected) {
      setEmailOk(true);
      setEmailMsg("Gmail connected.");
      refreshEmailStatus();
      setSearchParams({}, { replace: true });
    } else if (error) {
      setEmailOk(false);
      setEmailMsg(`Gmail connect failed: ${error}`);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const onProviderChange = (p: Provider) => {
    setProvider(p);
    const preset = PROVIDER_PRESETS[p];
    if (!baseUrl || baseUrl === PROVIDER_PRESETS.openai.base_url || baseUrl === PROVIDER_PRESETS.ollama.base_url) {
      setBaseUrl(preset.base_url);
    }
    if (!model || model === PROVIDER_PRESETS.openai.model || model === PROVIDER_PRESETS.ollama.model) {
      setModel(preset.model);
    }
  };

  const onTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testLLM({
        provider,
        base_url: baseUrl,
        model,
        api_key: apiKey ? apiKey : undefined,
      });
      setTestResult(result);
    } catch (e) {
      setTestResult({
        ok: false,
        provider,
        model,
        error: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setTesting(false);
    }
  };

  const onSave = async () => {
    setSaving(true);
    setSaveMsg(null);
    try {
      const next = await api.updateSettings({
        llm_provider: provider,
        llm_base_url: baseUrl,
        llm_model: model,
        llm_temperature: temperature,
        llm_timeout_s: timeoutS,
        apply_vision_enabled: applyVisionEnabled,
        apply_vision_model: applyVisionModel,
        ...(apiKey ? { llm_api_key: apiKey } : {}),
      });
      applyServerState(next);
      setApiKey("");
      setSaveOk(true);
      setSaveMsg("Settings saved.");
    } catch (e) {
      setSaveOk(false);
      setSaveMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const onSyncEmail = async () => {
    setEmailBusy(true);
    setEmailMsg(null);
    try {
      const result = await api.syncEmailAlerts();
      setSyncResult(result);
      setEmailOk(true);
      const parts = [
        `Scanned ${result.messages_scanned ?? 0} email${result.messages_scanned === 1 ? "" : "s"}`,
        `found ${result.jobs_found ?? 0}`,
        `new ${result.jobs_upserted ?? 0}`,
        `scored ${result.jobs_scored ?? 0}`,
      ];
      if ((result.approval_queued ?? 0) > 0) {
        parts.push(`queued ${result.approval_queued} for approval`);
      }
      setEmailMsg(parts.join(" · "));
      await refreshEmailStatus();
    } catch (e) {
      setEmailOk(false);
      setEmailMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setEmailBusy(false);
    }
  };

  const onDisconnectEmail = async () => {
    setEmailBusy(true);
    setEmailMsg(null);
    setSyncResult(null);
    try {
      await api.disconnectEmail();
      setEmailOk(true);
      setEmailMsg("Inbox disconnected.");
      await refreshEmailStatus();
    } catch (e) {
      setEmailOk(false);
      setEmailMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setEmailBusy(false);
    }
  };

  if (!loaded) {
    return <div className="spinner">Loading settings…</div>;
  }

  return (
    <>
      <h1>Settings</h1>
      <p>
        These values are persisted to <span className="code">backend/.env</span>. The
        same abstraction is used by every agent, so switching providers later won't
        break anything.
      </p>

      <div className="card">
        <h2>LLM provider</h2>
        <div className="field">
          <label>Provider</label>
          <select value={provider} onChange={(e) => onProviderChange(e.target.value as Provider)}>
            <option value="openai">OpenAI-compatible (OpenAI, OpenRouter, Groq, Gemini, …)</option>
            <option value="ollama">Ollama (local)</option>
          </select>
        </div>

        <div className="field-row">
          <div className="field">
            <label>Base URL</label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={PROVIDER_PRESETS[provider].base_url}
            />
          </div>
          <div className="field">
            <label>Model</label>
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={PROVIDER_PRESETS[provider].model}
            />
          </div>
        </div>

        {provider === "openai" && (
          <div className="field">
            <label>
              API key{" "}
              {hasStoredKey && (
                <span className="muted">
                  · a key is already stored — leave blank to keep it
                </span>
              )}
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={hasStoredKey ? "•••••••• (saved)" : "sk-…"}
              autoComplete="off"
            />
          </div>
        )}

        <div className="field-row">
          <div className="field">
            <label>Temperature ({temperature.toFixed(2)})</label>
            <input
              type="number"
              step={0.05}
              min={0}
              max={2}
              value={temperature}
              onChange={(e) => setTemperature(Number(e.target.value))}
            />
          </div>
          <div className="field">
            <label>Timeout (seconds)</label>
            <input
              type="number"
              step={5}
              min={1}
              max={600}
              value={timeoutS}
              onChange={(e) => setTimeoutS(Number(e.target.value))}
            />
          </div>
        </div>

        <div className="field">
          <label>
            <input
              type="checkbox"
              checked={applyVisionEnabled}
              onChange={(e) => setApplyVisionEnabled(e.target.checked)}
            />{" "}
            Use multimodal browser navigation for Apply
          </label>
          <p className="muted small">
            When deterministic Playwright actions stall, JobBee sends the screenshot,
            visible controls, and form summary to a vision-capable model for the next
            safe action. Final submit still requires your approval.
          </p>
        </div>

        <div className="field">
          <label>Apply vision model override</label>
          <input
            type="text"
            value={applyVisionModel}
            onChange={(e) => setApplyVisionModel(e.target.value)}
            placeholder={
              provider === "ollama"
                ? "blank = use LLM model, e.g. gemma4:e4b"
                : "blank = use configured LLM model or OpenRouter vision default"
            }
          />
        </div>

        <div className="button-row">
          <button onClick={onSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button className="secondary" onClick={onTest} disabled={testing}>
            {testing ? "Testing…" : "Test connection"}
          </button>
        </div>

        {saveMsg && (
          <div className={`notice ${saveOk ? "ok" : "bad"}`}>{saveMsg}</div>
        )}

        {testResult && (
          <div className={`notice ${testResult.ok ? "ok" : "bad"}`}>
            {testResult.ok ? (
              <>
                Got reply from <span className="code">{testResult.model}</span>:{" "}
                <span className="code">{testResult.content}</span>
                {testResult.completion_tokens != null && (
                  <span className="muted">
                    {" "}
                    · {testResult.prompt_tokens ?? "?"} prompt /{" "}
                    {testResult.completion_tokens} completion tokens
                  </span>
                )}
              </>
            ) : (
              <>Test failed: {testResult.error}</>
            )}
          </div>
        )}
      </div>

      <div className="card">
        <h2>Job alert email</h2>
        <GmailConnect
          emailStatus={emailStatus}
          onStatusChange={async () => {
            await refreshEmailStatus();
            setEmailOk(true);
            setEmailMsg("Gmail connected.");
          }}
        />

        {emailStatus?.connected && (
          <div className="button-row">
            <button onClick={onSyncEmail} disabled={emailBusy}>
              {emailBusy ? "Syncing…" : "Sync job alerts now"}
            </button>
            <button className="secondary" onClick={onDisconnectEmail} disabled={emailBusy}>
              Disconnect
            </button>
          </div>
        )}

        {emailStatus?.connected && emailStatus.last_sync_at && (
          <p className="muted small">
            Last sync {new Date(emailStatus.last_sync_at).toLocaleString()}
          </p>
        )}

        {emailMsg && <div className={`notice ${emailOk ? "ok" : "bad"}`}>{emailMsg}</div>}

        {syncResult && (syncResult.warnings?.length ?? 0) > 0 && (
          <div className="notice bad">
            {(syncResult.warnings ?? []).slice(0, 5).join(" · ")}
          </div>
        )}

        {syncResult && (syncResult.jobs?.length ?? 0) > 0 && (
          <div className="job-list" style={{ marginTop: 16 }}>
            {(syncResult.jobs ?? []).map((job) => (
              <article key={job.url} className="job-card">
                <div className="job-card-head">
                  <div>
                    <div className="job-title">{job.title}</div>
                    <div className="muted small">
                      {job.company ?? "Unknown company"}
                      {job.location ? ` · ${job.location}` : ""}
                      {" · "}
                      <span className="code">{(job.alert_source ?? "email").replace("email_", "")}</span>
                    </div>
                    {job.email_subject && (
                      <div className="muted small">From email: {job.email_subject}</div>
                    )}
                  </div>
                </div>
                {job.snippet && (
                  <p className="job-desc">{decodeHtml(job.snippet).slice(0, 280)}</p>
                )}
                <div className="button-row">
                  <a href={job.url} target="_blank" rel="noreferrer">
                    <button className="secondary">View job</button>
                  </a>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
