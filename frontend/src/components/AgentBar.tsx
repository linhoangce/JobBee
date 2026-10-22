import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type AgentStatus } from "../api";
import { timeAgo, usd, when } from "../lib/format";
import Icon from "./Icon";
import { useAction } from "./Toast";

/** What the agent is looking for, what it's doing, and the two things you can ask of it. */
export default function AgentBar({
  agent,
  onChanged,
}: {
  agent: AgentStatus;
  onChanged: () => void;
}) {
  const { busy, run } = useAction();
  const last = agent.last_search;

  return (
    <section className="card agent-bar">
      <div className="row between nowrap" style={{ alignItems: "flex-start" }}>
        <div className="grow">
          <div className="eyebrow">Looking for</div>
          <div className="agent-looking">{agent.looking_for}</div>
        </div>
        <div className="row nowrap" style={{ gap: 4 }}>
          <Link to="/settings#preferences" className="btn btn-sm ghost">
            Edit
          </Link>
          {agent.paused ? (
            <button
              className="sm primary"
              disabled={busy}
              onClick={() => void run(() => api.resume(), "Back on.").then(onChanged)}
            >
              <Icon name="play" />
              Resume
            </button>
          ) : (
            <button
              className="sm ghost"
              disabled={busy}
              title="Stop new searches and applications until you resume"
              onClick={() =>
                void run(() => api.pause(), "Paused. Nothing new starts until you resume.").then(
                  onChanged,
                )
              }
            >
              <Icon name="pause" />
              Pause
            </button>
          )}
        </div>
      </div>

      <div className="agent-meta" style={{ marginTop: 10 }}>
        {agent.searching ? (
          <span style={{ color: "var(--info)" }}>
            <span className="spinner" />
            {agent.search_stage ?? "Searching"}…
          </span>
        ) : agent.paused ? (
          <span style={{ color: "var(--warn)" }}>
            <Icon name="pause" />
            Paused: no searches or applications start
          </span>
        ) : agent.next_search_at ? (
          <span>
            <Icon name="clock" />
            {new Date(agent.next_search_at).getTime() <= Date.now() + 60_000
              ? "Searching shortly"
              : `Next search ${when(agent.next_search_at)}`}
          </span>
        ) : null}
        <span>
          <Icon name="send" />
          {agent.sent_today} of {agent.jobs_per_day} sent today
        </span>
        <span title="Claude usage today">
          <Icon name="money" />
          {usd(agent.spend_today_usd)} today
        </span>
      </div>

      {last && !agent.searching && last.status === "done" && (
        <div className="small faint" style={{ marginTop: 6 }}>
          Last search {timeAgo(last.finished_at ?? last.started_at)}: found {last.found}, kept{" "}
          {last.kept}, sent {last.sent}.{last.error ? ` Some sources failed: ${last.error}` : ""}
        </div>
      )}
      {last && !agent.searching && last.status !== "done" && (
        <div className="notice bad small" style={{ marginTop: 12 }}>
          <Icon name="alert" />
          <span>
            The last search {timeAgo(last.started_at)} didn't finish: {last.error ?? last.status}
          </span>
        </div>
      )}

      {agent.budget_reached && (
        <div className="notice warn small" style={{ marginTop: 12 }}>
          <Icon name="alert" />
          <span>
            Today's Claude budget ({usd(agent.daily_budget_usd)}) is used up, so JobBee waits until
            tomorrow. <Link to="/settings#claude">Change the budget</Link>
          </span>
        </div>
      )}

      <div className="agent-actions">
        <button
          className="primary"
          disabled={busy || agent.searching}
          onClick={() =>
            void run(() => api.searchNow(), "Searching for jobs…").then((ok) => ok && onChanged())
          }
        >
          <Icon name="search" />
          {agent.searching ? "Searching…" : "Find jobs now"}
        </button>
        <LinkInput onAdded={onChanged} />
      </div>
    </section>
  );
}

function LinkInput({ onAdded }: { onAdded: () => void }) {
  const [url, setUrl] = useState("");
  const { busy, run } = useAction();

  const submit = () =>
    void run(
      () => api.addLink(url.trim()),
      (r) => (r as { message: string }).message,
    ).then((ok) => {
      if (ok) {
        setUrl("");
        onAdded();
      }
    });

  return (
    <form
      className="input-row link-input"
      onSubmit={(e) => {
        e.preventDefault();
        if (url.trim()) submit();
      }}
    >
      <input
        value={url}
        inputMode="url"
        placeholder="Paste a job link (Greenhouse, Lever, Workday…)"
        onChange={(e) => setUrl(e.target.value)}
        aria-label="Job posting link"
      />
      <button disabled={busy || url.trim().length < 8}>
        {busy ? <span className="spinner" /> : <Icon name="link" />}
        Add
      </button>
    </form>
  );
}
