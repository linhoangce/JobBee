import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import AgentBar from "../components/AgentBar";
import ApplicationDrawer from "../components/ApplicationDrawer";
import ApplicationsList from "../components/ApplicationsList";
import ApplyingList from "../components/ApplyingList";
import { cardKey, FeedCard } from "../components/cards";
import Icon, { BeeMark } from "../components/Icon";
import { useAction } from "../components/Toast";
import { useHome } from "../hooks/useHome";
import { when } from "../lib/format";

/** The inbox: what the agent needs from you, what it's doing, and your history. */
export default function Home() {
  const { home, error, refresh } = useHome();
  const [openId, setOpenId] = useState<number | null>(null);
  const { busy, run } = useAction();

  if (!home) {
    return error ? (
      <div className="notice bad">
        <Icon name="alert" />
        {error}
      </div>
    ) : (
      <HomeSkeleton />
    );
  }

  const { agent, needs_you: cards } = home;
  const attention = cards.filter((c) => c.type !== "preparing").length;

  return (
    <>
      {error && (
        <div className="notice bad small" style={{ marginBottom: 12 }}>
          <Icon name="alert" />
          {error}
        </div>
      )}
      <AgentBar agent={agent} onChanged={refresh} />

      <section className="section">
        <div className="section-head">
          <h2>Needs you</h2>
          {attention > 0 && <span className="count">{attention}</span>}
        </div>
        {cards.length > 0 ? (
          <div className="feed">
            {cards.map((card) => (
              <FeedCard key={cardKey(card)} card={card} onChanged={refresh} />
            ))}
          </div>
        ) : (
          <div className="card empty">
            <BeeMark className="hex" />
            <h3>All caught up</h3>
            <p className="small" style={{ marginTop: 4 }}>
              {agent.searching
                ? "Searching now. New matches show up here."
                : agent.next_search_at
                  ? `JobBee looks again ${when(agent.next_search_at)}. Paste a link above anytime.`
                  : "Paste a job link above, or press Find jobs now."}
            </p>
            {agent.shortlisted > 0 && !agent.searching && (
              <button
                style={{ marginTop: 14 }}
                disabled={busy}
                onClick={() =>
                  void run(
                    () => api.prepareMore(Math.min(3, agent.shortlisted)),
                    "Tailoring your CV for the next ones…",
                  ).then(refresh)
                }
              >
                <Icon name="sparkle" />
                Show {Math.min(3, agent.shortlisted)} more good match
                {Math.min(3, agent.shortlisted) > 1 ? "es" : ""}
              </button>
            )}
          </div>
        )}
      </section>

      {home.applying.length > 0 && (
        <section className="section">
          <div className="section-head">
            <h2>Applying now</h2>
            <span className="small faint">One at a time. Nothing is submitted without you.</span>
          </div>
          <ApplyingList rows={home.applying} paused={agent.paused} onOpen={setOpenId} onChanged={refresh} />
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2>Your applications</h2>
        </div>
        <ApplicationsList rows={home.applications} counts={home.counts} onOpen={setOpenId} />
      </section>

      {!home.setup.telegram && (
        <p className="small faint" style={{ marginTop: 28, textAlign: "center" }}>
          Want these cards on your phone? <Link to="/settings#phone">Connect Telegram</Link>
        </p>
      )}

      {openId != null && (
        <ApplicationDrawer applicationId={openId} onClose={() => setOpenId(null)} onChanged={refresh} />
      )}
    </>
  );
}

function HomeSkeleton() {
  return (
    <div className="stack">
      <div className="card">
        <div className="skeleton" style={{ height: 14, width: 90 }} />
        <div className="skeleton" style={{ height: 24, width: "70%", marginTop: 10 }} />
        <div className="skeleton" style={{ height: 14, width: "50%", marginTop: 14 }} />
        <div className="skeleton" style={{ height: 40, marginTop: 18 }} />
      </div>
      <div className="skeleton" style={{ height: 220, borderRadius: 16, marginTop: 24 }} />
    </div>
  );
}
