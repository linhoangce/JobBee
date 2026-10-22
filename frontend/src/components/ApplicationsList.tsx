import { useState } from "react";
import type { ApplicationGroup, ApplicationRow } from "../api";
import { initials, timeAgo } from "../lib/format";
import Icon from "./Icon";

const TABS: { key: ApplicationGroup; label: string }[] = [
  { key: "applied", label: "Applied" },
  { key: "in_progress", label: "In progress" },
  { key: "skipped", label: "Skipped" },
  { key: "not_a_fit", label: "Not a fit" },
  { key: "didnt_finish", label: "Didn't finish" },
];

const BADGE: Record<ApplicationGroup, string> = {
  applied: "ok",
  in_progress: "accent",
  skipped: "",
  not_a_fit: "",
  didnt_finish: "bad",
};

/** Your history, by outcome. Click a row for details. */
export default function ApplicationsList({
  rows,
  counts,
  onOpen,
}: {
  rows: ApplicationRow[];
  counts: Record<string, number>;
  onOpen: (applicationId: number) => void;
}) {
  const firstNonEmpty = TABS.find((t) => (counts[t.key] ?? 0) > 0)?.key ?? "applied";
  const [tab, setTab] = useState<ApplicationGroup | null>(null);
  const active = tab ?? firstNonEmpty;
  const visible = rows.filter((r) => r.group === active);

  return (
    <>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={active === t.key}
            className={active === t.key ? "on" : ""}
            onClick={() => setTab(t.key)}
          >
            {t.label} <span className="n">{counts[t.key] ?? 0}</span>
          </button>
        ))}
      </div>
      {visible.length === 0 ? (
        <div className="card empty small">Nothing here yet.</div>
      ) : (
        <div className="list">
          {visible.map((row) => (
            <button key={row.id} className="list-row" onClick={() => onOpen(row.id)}>
              <div className="avatar">{initials(row.job.company ?? row.job.title)}</div>
              <div className="grow">
                <div className="strong truncate">{row.job.title}</div>
                <div className="small muted truncate">
                  {row.job.company ?? "Unknown company"}
                  {row.stage_reason && row.group !== "applied" ? ` · ${row.stage_reason}` : ""}
                </div>
              </div>
              <div className="stack tight" style={{ alignItems: "flex-end" }}>
                <span className={`badge ${BADGE[row.group]}`}>{row.label}</span>
                <span className="tiny faint">{timeAgo(row.submitted_at ?? row.updated_at)}</span>
              </div>
              <Icon name="chevron" className="icon faint" />
            </button>
          ))}
        </div>
      )}
    </>
  );
}
