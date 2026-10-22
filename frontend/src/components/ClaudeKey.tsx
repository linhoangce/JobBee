import { useEffect, useState } from "react";
import { api, type ClaudeStatus } from "../api";
import Icon from "./Icon";
import { errorText } from "./Toast";

/**
 * Paste an Anthropic API key; it's checked with Anthropic and kept in Windows Credential Manager.
 * Keys made for a whole organization also need the workspace ID to use.
 */
export default function ClaudeKey({
  onStatus,
  allowReplace = false,
}: {
  onStatus?: (status: ClaudeStatus) => void;
  allowReplace?: boolean;
}) {
  const [status, setStatus] = useState<ClaudeStatus | null>(null);
  const [key, setKey] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [showWorkspace, setShowWorkspace] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .claude()
      .then((s) => {
        setStatus(s);
        setWorkspace(s.workspace_id ?? "");
        setShowWorkspace(Boolean(s.workspace_id));
        onStatus?.(s);
      })
      .catch((e) => setError(errorText(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const keepingKey = Boolean(status?.connected) && !key.trim();
  const canSave = key.trim().length >= 20 || (keepingKey && workspace.trim() !== (status?.workspace_id ?? ""));

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await api.saveClaudeKey({
        apiKey: key.trim() || undefined,
        workspaceId: workspace.trim(),
      });
      setStatus(next);
      setKey("");
      setEditing(false);
      onStatus?.(next);
    } catch (e) {
      const message = errorText(e);
      if (message.toLowerCase().includes("workspace")) setShowWorkspace(true);
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  if (status == null) {
    return error ? (
      <div className="notice bad">{error}</div>
    ) : (
      <div className="skeleton" style={{ height: 44 }} />
    );
  }

  if (status.connected && !editing) {
    return (
      <div className="notice ok">
        <Icon name="check" />
        <div className="grow">
          <div className="strong">Claude is connected</div>
          <div className="small muted">
            {status.source === "app"
              ? "Your key is stored in Windows Credential Manager on this PC."
              : "Using ANTHROPIC_API_KEY from backend/.env."}
            {status.workspace_id ? ` Workspace ${status.workspace_id}.` : ""}
          </div>
        </div>
        {allowReplace && (
          <button className="sm ghost" onClick={() => setEditing(true)}>
            Change
          </button>
        )}
      </div>
    );
  }

  // A backend started before the workspace update doesn't report this field.
  if (status.workspace_id === undefined) {
    return (
      <div className="notice warn">
        <Icon name="alert" />
        <div className="grow">
          <div className="strong">Restart JobBee's backend to change the key</div>
          <div className="small" style={{ marginTop: 2 }}>
            It's still running the previous version. Press Ctrl+C in its terminal, start it again,
            then reload this page.
          </div>
          <button className="sm" style={{ marginTop: 8 }} onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="stack">
      {!status.connected && (
        <ol className="steps small muted">
          <li>
            Open{" "}
            <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer">
              console.anthropic.com → API keys
            </a>{" "}
            and create a key, ideally inside a workspace.
          </li>
          <li>
            Make sure the account has credit. Max and Team plans include monthly API credits: link
            the organization under claude.ai → Settings → Billing.
          </li>
          <li>Paste the key here.</li>
        </ol>
      )}
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSave) void save();
        }}
      >
        <div className="input-row">
          <input
            type="password"
            autoComplete="off"
            value={key}
            placeholder={status.connected ? "New key (leave empty to keep the current one)" : "sk-ant-…"}
            onChange={(e) => setKey(e.target.value)}
            aria-label="Anthropic API key"
          />
          <button className="primary" disabled={busy || !canSave}>
            {busy ? <span className="spinner" /> : <Icon name="key" />}
            {busy ? "Checking…" : status.connected ? "Save" : "Connect"}
          </button>
        </div>
        {showWorkspace ? (
          <div className="field">
            <label>Workspace ID</label>
            <input
              value={workspace}
              placeholder="wrkspc_…"
              onChange={(e) => setWorkspace(e.target.value)}
              aria-label="Anthropic workspace ID"
            />
            <span className="hint">
              Only for keys made for the whole organization. Find it in the Console under Settings →
              Workspaces.
            </span>
          </div>
        ) : (
          <button
            type="button"
            className="link small"
            style={{ alignSelf: "flex-start" }}
            onClick={() => setShowWorkspace(true)}
          >
            My key is for the whole organization
          </button>
        )}
      </form>
      {editing && (
        <button
          className="link small"
          style={{ alignSelf: "flex-start" }}
          onClick={() => {
            setEditing(false);
            setError(null);
            setWorkspace(status.workspace_id ?? "");
          }}
        >
          Cancel
        </button>
      )}
      {error && (
        <div className="notice bad small">
          <Icon name="alert" />
          {error}
        </div>
      )}
    </div>
  );
}
