import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Home } from "../api";

const FAST_MS = 2000;
const SLOW_MS = 15000;

/** Polls /api/home: every 2s while something is happening, every 15s otherwise. */
export function useHome() {
  const [home, setHome] = useState<Home | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      setHome(await api.home());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      inFlight.current = false;
    }
  }, []);

  const live =
    home != null &&
    (home.agent.searching ||
      home.applying.length > 0 ||
      home.needs_you.some((c) => c.type === "preparing" || c.type === "review"));

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const timer = window.setInterval(tick, live ? FAST_MS : SLOW_MS);
    document.addEventListener("visibilitychange", tick);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [live, refresh]);

  return { home, error, refresh };
}
