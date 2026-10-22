import { useCallback, useEffect, useState } from "react";
import { api, type MessagingStatus } from "../api";

const POLL_MS = 2000;

type Props = {
  onConnected?: () => void;
};

/** Create-a-bot guide → token → pairing link → live "Connected ✓". Reused by onboarding. */
export default function TelegramConnect({ onConnected }: Props) {
  const [status, setStatus] = useState<MessagingStatus | null>(null);
  const [token, setToken] = useState("");
  const [linkUrl, setLinkUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api.getMessagingStatus();
      setStatus((prev) => {
        if (prev && !prev.linked && next.linked) onConnected?.();
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [onConnected]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const waitingForPairing = Boolean(linkUrl) && status != null && !status.linked;
  useEffect(() => {
    if (!waitingForPairing) return;
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [waitingForPairing, refresh]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (status == null) return <p className="muted small">Checking Telegram…</p>;

  if (status.linked) {
    return (
      <div>
        <div className="notice ok">
          Connected to <strong>@{status.bot_username}</strong>. JobBee will message you there.
        </div>
        {status.last_error && <div className="notice bad">Last error: {status.last_error}</div>}
        {notice && <div className="notice ok">{notice}</div>}
        {error && <div className="notice bad">{error}</div>}
        <div className="row">
          <button
           
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await api.sendTestMessage();
                setNotice("Sent — check Telegram.");
              })
            }
          >
            Send test message
          </button>
          <button
            className="ghost danger"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await api.disconnectTelegram();
                setLinkUrl(null);
              })
            }
          >
            Disconnect
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      {!status.bot_configured || !linkUrl ? (
        <>
          <ol className="steps small">
            <li>
              Open{" "}
              <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">
                @BotFather
              </a>{" "}
              in Telegram and send <span className="code">/newbot</span>.
            </li>
            <li>Pick any name, then a username ending in “bot” (e.g. lin_jobbee_bot).</li>
            <li>Copy the token BotFather replies with and paste it here.</li>
          </ol>
          <div className="input-row">
            <input
              type="password"
              value={token}
              placeholder="123456789:AA…"
              onChange={(e) => setToken(e.target.value)}
            />
            <button
              disabled={busy || token.trim().length < 20}
              onClick={() =>
                void run(async () => {
                  const link = await api.setupTelegram(token.trim());
                  setLinkUrl(link.link_url);
                  setToken("");
                })
              }
            >
              {busy ? "Checking…" : "Connect"}
            </button>
          </div>
          {status.bot_configured && !linkUrl && (
            <button
              className="link small"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const link = await api.newTelegramLink();
                  setLinkUrl(link.link_url);
                })
              }
            >
              Already added @{status.bot_username}? Get a new pairing link
            </button>
          )}
        </>
      ) : (
        <>
          <p className="small">
            Last step: open your bot and press <strong>Start</strong>. This page updates by itself.
          </p>
          <div className="row">
            <a className="btn primary" href={linkUrl} target="_blank" rel="noreferrer">
              Open Telegram
            </a>
          </div>
          <p className="muted small">Waiting for you to press Start… (link valid for 10 minutes)</p>
        </>
      )}
      {error && <div className="notice bad">{error}</div>}
    </div>
  );
}
