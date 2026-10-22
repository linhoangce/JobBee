import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import Icon from "./Icon";

type Toast = { id: number; text: string; bad: boolean };
type Notify = (text: string, opts?: { bad?: boolean }) => void;

const ToastContext = createContext<Notify>(() => {});
let nextId = 1;

/** One place for "Done" / "Something went wrong" messages, bottom-centre. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const notify = useCallback<Notify>((text, opts) => {
    const id = nextId++;
    setToasts((prev) => [...prev.slice(-2), { id, text, bad: Boolean(opts?.bad) }]);
    window.setTimeout(
      () => setToasts((prev) => prev.filter((t) => t.id !== id)),
      opts?.bad ? 7000 : 3500,
    );
  }, []);

  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.bad ? "bad" : ""}`}>
            <Icon name={t.bad ? "alert" : "check"} />
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): Notify {
  return useContext(ToastContext);
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Run an action, toast its message (or its error), and report whether it worked. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async (fn: () => Promise<unknown>, success?: string | null | ((result: unknown) => string | null)) => {
      setBusy(true);
      try {
        const result = await fn();
        const message = typeof success === "function" ? success(result) : success;
        if (message) toast(message);
        return true;
      } catch (e) {
        toast(errorText(e), { bad: true });
        return false;
      } finally {
        setBusy(false);
      }
    },
    [toast],
  );
  return { busy, run };
}
