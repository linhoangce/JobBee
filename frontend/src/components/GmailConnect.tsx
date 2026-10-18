import { useEffect, useState } from "react";
import { api, type EmailStatus } from "../api";

type GmailConnectProps = {
  emailStatus: EmailStatus | null;
  onStatusChange?: () => void | Promise<void>;
  compact?: boolean;
};

export default function GmailConnect({ emailStatus, onStatusChange, compact = false }: GmailConnectProps) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [ok, setOk] = useState(true);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const allowedOrigins = new Set([
        window.location.origin,
        "http://127.0.0.1:8000",
        "http://localhost:8000",
      ]);
      if (!allowedOrigins.has(event.origin)) return;
      const data = event.data as { type?: string; ok?: boolean; message?: string } | null;
      if (!data || data.type !== "jobbee:gmail-oauth") return;
      setBusy(false);
      setOk(Boolean(data.ok));
      setMessage(data.message || (data.ok ? "Gmail connected." : "Could not connect Gmail."));
      void onStatusChange?.();
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onStatusChange]);

  const onSignInWithGoogle = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const { authorization_url } = await api.startGmailConnect(true);
      const popup = window.open(
        authorization_url,
        "jobbee-gmail-oauth",
        "width=520,height=720,menubar=no,toolbar=no,status=no",
      );
      if (!popup) {
        window.location.href = authorization_url;
        return;
      }
      const timer = window.setInterval(() => {
        if (popup.closed) {
          window.clearInterval(timer);
          setBusy(false);
          void onStatusChange?.();
        }
      }, 500);
    } catch (e) {
      setOk(false);
      setMessage(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  if (emailStatus?.connected) {
    return (
      <div className="gmail-connect">
        <div className="notice ok">
          Connected as <span className="code">{emailStatus.connected_email ?? "Gmail"}</span>
        </div>
      </div>
    );
  }

  const oauthReady = emailStatus?.gmail_oauth_available;

  return (
    <div className={`gmail-connect ${compact ? "compact" : ""}`}>
      {!compact && (
        <p>
          Import job alerts from LinkedIn, Indeed, and similar senders. JobBee only reads your inbox —
          it never sends email.
        </p>
      )}

      {oauthReady ? (
        <button
          type="button"
          className="google-signin-btn"
          onClick={onSignInWithGoogle}
          disabled={busy}
        >
          <GoogleMark />
          {busy ? "Waiting for Google…" : "Sign in with Google"}
        </button>
      ) : (
        <div className="notice bad">
          Gmail sign-in is not available on this install yet.
        </div>
      )}

      {message && <div className={`notice ${ok ? "ok" : "bad"}`}>{message}</div>}
    </div>
  );
}

function GoogleMark() {
  return (
    <svg className="google-mark" viewBox="0 0 48 48" aria-hidden="true">
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.56 2.98-2.26 5.5-4.82 7.18l7.73 6c4.51-4.16 7.11-10.28 7.11-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  );
}
