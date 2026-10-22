import { api, type ApplyingRow } from "../api";
import { describeEvent } from "../lib/format";
import Icon from "./Icon";
import { useAction } from "./Toast";

const STATUS: Record<string, string> = {
  queued: "Waiting its turn",
  starting: "Opening the application",
  running: "Filling in the form",
  needs_user: "Waiting for your answer",
  needs_human: "Waiting for you at your PC",
  submitting: "Submitting",
};

/** Applications the agent is filling right now (one at a time; the rest queue). */
export default function ApplyingList({
  rows,
  paused,
  onOpen,
  onChanged,
}: {
  rows: ApplyingRow[];
  paused: boolean;
  onOpen: (applicationId: number) => void;
  onChanged: () => void;
}) {
  return (
    <div className="list">
      {rows.map((row) => (
        <ApplyingItem key={row.run_id} row={row} paused={paused} onOpen={onOpen} onChanged={onChanged} />
      ))}
    </div>
  );
}

function ApplyingItem({
  row,
  paused,
  onOpen,
  onChanged,
}: {
  row: ApplyingRow;
  paused: boolean;
  onOpen: (applicationId: number) => void;
  onChanged: () => void;
}) {
  const { busy, run } = useAction();
  const step = row.last_event ? describeEvent(row.last_event) : null;
  const waiting = row.status === "queued";
  const detail =
    waiting && paused
      ? "Paused: resume to start"
      : row.status === "running" && step
        ? step
        : STATUS[row.status] ?? row.status;

  return (
    <div className="list-row">
      {waiting ? <Icon name="clock" className="icon faint" /> : <span className="spinner" />}
      <button className="row-link grow" onClick={() => onOpen(row.application_id)}>
        <span className="strong truncate">
          {row.job.title}
          {row.job.company ? ` · ${row.job.company}` : ""}
        </span>
        <span className="small muted truncate">{detail}</span>
      </button>
      {!waiting && (
        <button className="sm ghost" disabled={busy} onClick={() => void run(() => api.focusRun(row.run_id))}>
          <Icon name="monitor" />
          <span className="hide-sm">Show</span>
        </button>
      )}
      <button
        className="sm ghost danger"
        disabled={busy}
        onClick={() => void run(() => api.cancelRun(row.run_id), "Stopped.").then((ok) => ok && onChanged())}
      >
        Stop
      </button>
    </div>
  );
}
