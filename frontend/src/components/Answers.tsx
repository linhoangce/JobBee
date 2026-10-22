import { useState } from "react";
import type { AnswerSource, ApplyEvent, FilledField } from "../api";
import { describeEvent } from "../lib/format";

const SOURCES: Record<AnswerSource, { label: string; tone?: "warn" | "info" | "ok" }> = {
  profile: { label: "Profile" },
  resume: { label: "Resume" },
  standard_answer: { label: "Standard answer" },
  saved_answer: { label: "Saved answer" },
  judgement: { label: "JobBee's call", tone: "warn" },
  consent: { label: "Consent", tone: "info" },
  you: { label: "Your change", tone: "ok" },
};

const FLAGGED = new Set<string>(["judgement", "consent", "you"]);

// Event kinds worth a line in a run's summary (the full step log has the rest).
const REPORTED = new Set(["account_created", "account_signed_in", "account_rejected", "password_reset", "email_checked", "sensitive_blocked"]);

/** The answers a run filled in: JobBee's own calls, consents and your changes first. */
export function AnswerList({
  answers,
  onChange,
  disabled = false,
}: {
  answers: FilledField[];
  onChange?: (field: string, value: string) => Promise<boolean>;
  disabled?: boolean;
}) {
  const flagged = answers.filter((a) => FLAGGED.has(a.source ?? ""));
  const rest = answers.filter((a) => !FLAGGED.has(a.source ?? ""));
  const rows = (list: FilledField[]) => (
    <ul className="answer-list">
      {list.map((a, i) => (
        <AnswerRow key={`${a.field}-${i}`} answer={a} onChange={onChange} disabled={disabled} />
      ))}
    </ul>
  );
  return (
    <>
      {flagged.length > 0 && (
        <div className="answer-group">
          <div className="strong small">JobBee's calls and consents ({flagged.length})</div>
          {rows(flagged)}
        </div>
      )}
      {rest.length > 0 && (
        <details className="answer-group" open={flagged.length === 0 && rest.length <= 5}>
          <summary>From your profile and resume ({rest.length})</summary>
          {rows(rest)}
        </details>
      )}
    </>
  );
}

function AnswerRow({
  answer,
  onChange,
  disabled,
}: {
  answer: FilledField;
  onChange?: (field: string, value: string) => Promise<boolean>;
  disabled: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(answer.value);
  const [saving, setSaving] = useState(false);
  const source = SOURCES[answer.source ?? "profile"] ?? SOURCES.profile;
  const next = value.trim();

  return (
    <li className={`answer ${source.tone ?? ""}`}>
      <div className="answer-head">
        <span className="answer-field">{answer.field}</span>
        <span className={`source ${source.tone ?? ""}`}>{source.label}</span>
      </div>
      {editing && onChange ? (
        <form
          className="input-row"
          onSubmit={(e) => {
            e.preventDefault();
            setSaving(true);
            void onChange(answer.field, next).then((ok) => {
              setSaving(false);
              if (ok) setEditing(false);
            });
          }}
        >
          <input autoFocus value={value} aria-label={`New answer for ${answer.field}`} onChange={(e) => setValue(e.target.value)} />
          <button className="primary sm" disabled={saving || !next || next === answer.value}>
            Save
          </button>
          <button
            type="button"
            className="ghost sm"
            onClick={() => {
              setEditing(false);
              setValue(answer.value);
            }}
          >
            Cancel
          </button>
        </form>
      ) : (
        <div className="answer-value">
          <span>{answer.value || "—"}</span>
          {onChange && (
            <button className="link small" disabled={disabled} onClick={() => setEditing(true)}>
              Change
            </button>
          )}
        </div>
      )}
      {answer.note && <div className="small faint">{answer.note}</div>}
    </li>
  );
}

/** The headline facts of a run: accounts, emails, consents and fields left for you. */
export function RunHighlights({ events, answers }: { events: ApplyEvent[]; answers: FilledField[] }) {
  const lines = events
    .filter((e) => REPORTED.has(e.kind))
    .map((e) => ({ key: `e${e.id}`, text: describeEvent(e) }))
    .concat(
      answers
        .filter((a) => a.source === "consent")
        .map((a, i) => ({ key: `c${i}`, text: `Agreed to ${a.note || a.field}` })),
    )
    .filter((l): l is { key: string; text: string } => Boolean(l.text));
  if (lines.length === 0) return null;
  return (
    <ul className="highlights">
      {lines.map((l) => (
        <li key={l.key}>{l.text}</li>
      ))}
    </ul>
  );
}
