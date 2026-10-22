import { useState } from "react";
import Icon from "./Icon";

type Props = {
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  suggestions?: string[];
};

/** Type and press Enter (or a comma) to add; click × to remove. */
export default function ChipInput({ values, onChange, placeholder, suggestions = [] }: Props) {
  const [draft, setDraft] = useState("");

  const add = (raw: string) => {
    const parts = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const next = [...values];
    for (const p of parts) {
      if (!next.some((v) => v.toLowerCase() === p.toLowerCase())) next.push(p);
    }
    if (next.length !== values.length) onChange(next);
    setDraft("");
  };

  const unused = suggestions.filter((s) => {
    const low = s.toLowerCase();
    return !values.some((v) => low.includes(v.toLowerCase()) || v.toLowerCase().includes(low));
  });

  return (
    <div className="stack tight">
      <label className="chips-input">
        {values.map((v) => (
          <span key={v} className="chip">
            {v}
            <button
              type="button"
              aria-label={`Remove ${v}`}
              onClick={() => onChange(values.filter((x) => x !== v))}
            >
              <Icon name="x" />
            </button>
          </span>
        ))}
        <input
          value={draft}
          placeholder={values.length ? "Add more…" : placeholder}
          onChange={(e) => {
            if (e.target.value.endsWith(",")) add(e.target.value);
            else setDraft(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add(draft);
            } else if (e.key === "Backspace" && !draft && values.length) {
              onChange(values.slice(0, -1));
            }
          }}
          onBlur={() => {
            if (draft.trim()) add(draft);
          }}
        />
      </label>
      {unused.length > 0 && (
        <div className="row" style={{ gap: 6 }}>
          {unused.slice(0, 6).map((s) => (
            <button key={s} type="button" className="sm ghost" onClick={() => add(s)}>
              + {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
