import type { ApplyEvent } from "../api";

export function initials(name: string | null | undefined): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  return (words[0][0] + (words[1]?.[0] ?? "")).toUpperCase();
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** "9:00 AM", "tomorrow 9:00 AM" or "Mon 9:00 AM". */
export function when(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(d) - day(now)) / 86_400_000);
  if (diff <= 0) return clock(iso);
  if (diff === 1) return `tomorrow ${clock(iso)}`;
  return `${d.toLocaleDateString(undefined, { weekday: "short" })} ${clock(iso)}`;
}

export function minutesLeft(iso: string | null): number | null {
  if (!iso) return null;
  return Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 60_000));
}

export function usd(value: number): string {
  return value < 10 ? `$${value.toFixed(2)}` : `$${Math.round(value)}`;
}

function compact(value: unknown, limit = 90): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

const EMAIL_PURPOSES: Record<string, string> = {
  verify_email: "verification",
  reset_password: "password reset",
  sign_in_code: "sign-in code",
};

/** One line per agent step, for the progress line and the step log. */
export function describeEvent(event: Pick<ApplyEvent, "kind" | "data">): string | null {
  const d = event.data;
  switch (event.kind) {
    case "tool_call": {
      const tool = String(d.tool ?? "").replace(/^browser\./, "");
      const input = d.input as Record<string, unknown> | undefined;
      if (tool === "form_input") return `Filling in ${compact(input?.value, 50)}`;
      if (tool === "navigate") return "Opening the application";
      if (tool === "find") return `Looking for “${compact(input?.query, 50)}”`;
      if (tool === "file_upload") return "Uploading your CV";
      if (tool === "screenshot") return "Checking the page";
      if (tool === "scroll") return "Scrolling";
      if (tool === "left_click" || tool === "click") return "Clicking through the form";
      if (tool === "type") return "Typing";
      if (tool === "tool.site_account") return "Filling in the sign-in details";
      if (tool === "tool.check_inbox") return "Checking your inbox for the site's email";
      if (tool.startsWith("tool.")) return null;
      return tool ? tool.replace(/_/g, " ") : null;
    }
    case "text":
      return compact(d.text, 140);
    case "guard_block":
      return `Stopped a click on “${compact(d.label, 40)}”. Only you can submit.`;
    case "question":
      return `Asked you: ${compact(d.question, 110)}`;
    case "answer":
      return `You answered: ${compact(d.answer || "(skipped)", 70)}`;
    case "human_action":
      return `Handed to you: ${compact(d.message, 110)}`;
    case "human_done":
      return d.reply ? `You couldn't: ${compact(d.reply, 90)}` : "You finished at your PC";
    case "account_created":
      return `Filled the sign-up form on ${d.host} as ${d.login} (password saved on this PC)`;
    case "account_signed_in":
      return `Filled sign-in on ${d.host} as ${d.login}`;
    case "account_rejected":
      return `${d.host} refused the password, so JobBee won't try it again`;
    case "password_reset":
      return `Set a new password on ${d.host} (saved on this PC)`;
    case "email_checked":
      return d.found
        ? `Used the ${EMAIL_PURPOSES[String(d.purpose)] ?? "site's"} email from ${d.sender}`
        : `No email from ${d.host} arrived`;
    case "sensitive_blocked":
      return `Left a sensitive field for you${d.field ? `: ${compact(d.field, 60)}` : ""}`;
    case "revise":
      return `You asked to change ${(d.changes as unknown[] | undefined)?.length ?? 0} answer(s)`;
    case "revised":
      return d.ok ? "Changed the answers you corrected" : `Couldn't change them: ${compact(d.error, 100)}`;
    case "sensitive_refused":
      return `Skipped a sensitive question: ${compact(d.question, 80)}`;
    case "submit_click":
      return d.clicked
        ? "Clicked Submit (you approved it)"
        : `Couldn't submit: ${compact(d.detail, 100)}`;
    case "status":
      if (d.status === "running") return "Started";
      if (d.status === "needs_you") return "Set aside until you're at your PC";
      return `Status: ${String(d.status).replace(/_/g, " ")}`;
    case "error":
      return `Error: ${compact(d.message, 140)}`;
    case "finish":
      return `Finished: ${compact(d.summary, 140)}`;
    default:
      return null;
  }
}
