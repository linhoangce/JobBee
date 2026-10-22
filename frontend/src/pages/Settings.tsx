import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  api,
  type ApplicationDefaults,
  type ClaudeStatus,
  type DocumentInfo,
  type EmailStatus,
  type Preferences,
  type Profile,
  type SiteAccount,
  type WatchlistItem,
  type YesNo,
} from "../api";
import ClaudeKey from "../components/ClaudeKey";
import { StyleGallery } from "../components/DocumentStyle";
import ErrorBoundary from "../components/ErrorBoundary";
import GmailConnect from "../components/GmailConnect";
import Icon from "../components/Icon";
import PreferencesForm from "../components/PreferencesForm";
import ResumeImport from "../components/ResumeImport";
import TelegramConnect from "../components/TelegramConnect";
import { useAction, useToast } from "../components/Toast";
import { timeAgo, usd } from "../lib/format";

const SECTIONS = [
  { id: "preferences", label: "Job preferences" },
  { id: "sources", label: "Where to look" },
  { id: "applying", label: "Applying for you" },
  { id: "profile", label: "Resume & answers" },
  { id: "style", label: "CV style" },
  { id: "claude", label: "Claude" },
  { id: "phone", label: "Phone updates" },
];

export default function Settings() {
  const { hash } = useLocation();

  useEffect(() => {
    if (!hash) return;
    const el = document.getElementById(hash.slice(1));
    window.setTimeout(() => el?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }, [hash]);

  return (
    <>
      <div className="page-head">
        <h1>Settings</h1>
      </div>
      <nav className="settings-nav">
        {SECTIONS.map((s) => (
          <a key={s.id} href={`#${s.id}`}>
            {s.label}
          </a>
        ))}
      </nav>
      <ErrorBoundary label="Job preferences">
        <PreferencesSection />
      </ErrorBoundary>
      <ErrorBoundary label="Where to look">
        <SourcesSection />
      </ErrorBoundary>
      <ErrorBoundary label="Applying for you">
        <ApplyingSection />
      </ErrorBoundary>
      <ErrorBoundary label="Resume & answers">
        <ProfileSection />
      </ErrorBoundary>
      <ErrorBoundary label="CV style">
        <StyleSection />
      </ErrorBoundary>
      <ErrorBoundary label="Claude">
        <ClaudeSection />
      </ErrorBoundary>
      <section id="phone" className="settings-section">
        <div className="card">
          <h2>Phone updates</h2>
          <p className="muted">
            Optional. Get the same cards in Telegram and tap Apply or Submit from your phone. Your PC
            still does the work, so keep JobBee running.
          </p>
          <ErrorBoundary label="Phone updates">
            <TelegramConnect />
          </ErrorBoundary>
        </div>
      </section>
    </>
  );
}

// --- preferences -------------------------------------------------------------------------

function PreferencesSection() {
  const [saved, setSaved] = useState<Preferences | null>(null);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const { busy, run } = useAction();

  useEffect(() => {
    api.getPreferences().then((p) => {
      setSaved(p);
      setPrefs(p);
    });
  }, []);

  const dirty = JSON.stringify(saved) !== JSON.stringify(prefs);

  return (
    <section id="preferences" className="settings-section">
      <div className="card">
        <h2>Job preferences</h2>
        <p className="muted">What JobBee looks for, and how picky it is.</p>
        {prefs ? (
          <>
            <PreferencesForm value={prefs} onChange={setPrefs} extended />
            <div className="row" style={{ marginTop: 18 }}>
              <button
                className="primary"
                disabled={!dirty || busy}
                onClick={() =>
                  void run(async () => {
                    // Only what this form edits; the other sections save their own switches.
                    const {
                      web_search_enabled: _search,
                      document_style: _style,
                      agent_accounts: _accounts,
                      agent_consents: _consents,
                      ...edited
                    } = prefs;
                    const next = await api.savePreferences(edited);
                    setSaved(next);
                    setPrefs(next);
                  }, "Saved. The next search uses these.")
                }
              >
                Save
              </button>
              {dirty && (
                <button className="ghost" onClick={() => setPrefs(saved)}>
                  Undo changes
                </button>
              )}
            </div>
          </>
        ) : (
          <div className="skeleton" style={{ height: 300 }} />
        )}
      </div>
    </section>
  );
}

// --- sources -------------------------------------------------------------------------------

function SourcesSection() {
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [email, setEmail] = useState<EmailStatus | null>(null);
  const { run } = useAction();

  useEffect(() => {
    api.getPreferences().then(setPrefs);
    api.getEmailStatus().then(setEmail).catch(() => setEmail(null));
  }, []);

  return (
    <section id="sources" className="settings-section">
      <div className="card">
        <h2>Where JobBee looks</h2>
        <p className="muted">Every search uses all of these. Links you paste always work too.</p>

        <div className="setting-row">
          <Icon name="search" className="icon lg" />
          <div className="grow">
            <div className="strong">Web search</div>
            <div className="small muted">
              Claude searches Greenhouse, Lever, Ashby, Workday and other job boards for your roles.
            </div>
          </div>
          {prefs && (
            <button
              className={`switch ${prefs.web_search_enabled ? "on" : ""}`}
              role="switch"
              aria-checked={prefs.web_search_enabled}
              aria-label="Web search"
              onClick={() =>
                void run(async () => {
                  setPrefs(await api.savePreferences({ web_search_enabled: !prefs.web_search_enabled }));
                })
              }
            />
          )}
        </div>

        <div className="setting-row" style={{ alignItems: "flex-start" }}>
          <Icon name="building" className="icon lg" />
          <div className="grow">
            <div className="strong">Companies you'd love to work for</div>
            <div className="small muted" style={{ marginBottom: 10 }}>
              JobBee checks their job boards on every search (Greenhouse, Lever and Ashby).
            </div>
            <Watchlist />
          </div>
        </div>

        <div className="setting-row" style={{ alignItems: "flex-start" }}>
          <Icon name="mail" className="icon lg" />
          <div className="grow">
            <div className="strong">LinkedIn job alerts</div>
            <div className="small muted" style={{ marginBottom: 10 }}>
              Sign in with Google and JobBee reads your LinkedIn and Indeed alert emails, plus the
              sign-up emails job sites send to the accounts it makes. It never sends email.
            </div>
            <GmailConnect
              compact
              emailStatus={email}
              onStatusChange={() => api.getEmailStatus().then(setEmail)}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function Watchlist() {
  const [items, setItems] = useState<WatchlistItem[] | null>(null);
  const [text, setText] = useState("");
  const { busy, run } = useAction();

  useEffect(() => {
    api.watchlist().then(setItems);
  }, []);

  return (
    <div className="stack tight">
      <form
        className="input-row"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim().length < 2) return;
          void run(
            async () => {
              const item = await api.addCompany(text.trim());
              setItems((prev) => [...(prev ?? []).filter((i) => i.id !== item.id), item]);
              setText("");
              return item;
            },
            (item) => `Added ${(item as WatchlistItem).name}.`,
          );
        }}
      >
        <input
          value={text}
          placeholder="Company name or careers page link"
          onChange={(e) => setText(e.target.value)}
        />
        <button disabled={busy || text.trim().length < 2}>{busy ? <span className="spinner" /> : "Add"}</button>
      </form>
      {items && items.length > 0 && (
        <div className="list" style={{ marginTop: 6 }}>
          {items.map((item) => (
            <div key={item.id} className="list-row">
              <div className="grow">
                <div className="strong">{item.name}</div>
                <div className="tiny faint">
                  {item.ats} · {item.slug}
                  {item.last_polled_at ? ` · checked ${timeAgo(item.last_polled_at)}` : " · not checked yet"}
                  {item.last_error ? ` · ${item.last_error}` : ""}
                </div>
              </div>
              <button
                className="icon-btn"
                aria-label={`Remove ${item.name}`}
                onClick={() =>
                  void run(async () => {
                    await api.removeCompany(item.id);
                    setItems((prev) => (prev ?? []).filter((i) => i.id !== item.id));
                  })
                }
              >
                <Icon name="trash" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- applying for you ------------------------------------------------------------------------

function ApplyingSection() {
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [email, setEmail] = useState<EmailStatus | null>(null);
  const { run } = useAction();

  useEffect(() => {
    api.getPreferences().then(setPrefs);
    api.getEmailStatus().then(setEmail).catch(() => setEmail(null));
  }, []);

  const toggle = (key: "agent_accounts" | "agent_consents") =>
    prefs &&
    void run(async () => {
      setPrefs(await api.savePreferences({ [key]: !prefs[key] }));
    });

  return (
    <section id="applying" className="settings-section">
      <div className="card">
        <h2>Applying for you</h2>
        <p className="muted">
          JobBee fills each application by itself, start to finish, and you only tap Submit. Every
          application's report lists what it did, including anything below.
        </p>

        <div className="setting-row">
          <Icon name="key" className="icon lg" />
          <div className="grow">
            <div className="strong">Create and sign in to job-site accounts</div>
            <div className="small muted">
              Sites like Workday want an account per company. JobBee makes one with your email and a
              strong password it saves in Windows Credential Manager on this PC, and confirms it from
              your inbox.
            </div>
          </div>
          {prefs && (
            <button
              className={`switch ${prefs.agent_accounts ? "on" : ""}`}
              role="switch"
              aria-checked={prefs.agent_accounts}
              aria-label="Create and sign in to job-site accounts"
              onClick={() => toggle("agent_accounts")}
            />
          )}
        </div>
        {prefs?.agent_accounts && email && !email.connected && (
          <div className="notice info small">
            <Icon name="mail" />
            <span>
              Connect Gmail under <a href="#sources">Where to look</a> so JobBee can confirm new
              accounts by itself. Without it, those applications wait for you.
            </span>
          </div>
        )}

        <div className="setting-row">
          <Icon name="check" className="icon lg" />
          <div className="grow">
            <div className="strong">Accept terms and sign with your name</div>
            <div className="small muted">
              Account terms, privacy statements, background-check consent and "I certify this is
              accurate" signatures, typed with your full name and the date.
            </div>
          </div>
          {prefs && (
            <button
              className={`switch ${prefs.agent_consents ? "on" : ""}`}
              role="switch"
              aria-checked={prefs.agent_consents}
              aria-label="Accept terms and sign with your name"
              onClick={() => toggle("agent_consents")}
            />
          )}
        </div>

        <p className="small faint" style={{ marginTop: 12 }}>
          Never done by JobBee: CAPTCHAs, phone codes, ID numbers and bank details. Those applications
          wait on Home for when you're at your PC, and the rest carry on.
        </p>

        <SiteAccounts />
      </div>
    </section>
  );
}

function SiteAccounts() {
  const [accounts, setAccounts] = useState<SiteAccount[] | null>(null);
  const [shown, setShown] = useState<Record<number, string>>({});
  const { busy, run } = useAction();

  useEffect(() => {
    api.siteAccounts().then(setAccounts).catch(() => setAccounts([]));
  }, []);

  if (!accounts || accounts.length === 0) return null;
  return (
    <div style={{ marginTop: 16 }}>
      <div className="strong">Accounts JobBee made ({accounts.length})</div>
      <div className="list" style={{ marginTop: 8 }}>
        {accounts.map((a) => (
          <div key={a.id} className="list-row">
            <div className="grow">
              <div className="strong" style={{ overflowWrap: "anywhere" }}>
                {a.host}
              </div>
              <div className="tiny faint">
                {a.login} · made {timeAgo(a.created_at)}
                {a.last_used_at ? ` · used ${timeAgo(a.last_used_at)}` : ""}
                {a.status === "verified" ? " · email confirmed" : ""}
                {a.status === "rejected" ? " · the site refused this password; JobBee resets it next time" : ""}
              </div>
              {shown[a.id] && <code className="secret">{shown[a.id]}</code>}
            </div>
            <button
              className="sm ghost"
              disabled={busy}
              aria-label={`${shown[a.id] ? "Hide" : "Show"} the password for ${a.host}`}
              onClick={() =>
                shown[a.id]
                  ? setShown(({ [a.id]: _gone, ...rest }) => rest)
                  : void run(async () => {
                      const { password } = await api.revealSiteAccount(a.id);
                      setShown((prev) => ({ ...prev, [a.id]: password }));
                    })
              }
            >
              {shown[a.id] ? "Hide" : "Show"}
            </button>
            <button
              className="icon-btn"
              aria-label={`Forget the ${a.host} account`}
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api.forgetSiteAccount(a.id);
                  setAccounts((prev) => (prev ?? []).filter((x) => x.id !== a.id));
                }, "Forgotten. The account still exists on the site.")
              }
            >
              <Icon name="trash" />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- CV style ------------------------------------------------------------------------------------

function StyleSection() {
  const [style, setStyle] = useState<string | null>(null);

  useEffect(() => {
    api.getPreferences().then((p) => setStyle(p.document_style));
  }, []);

  return (
    <section id="style" className="settings-section">
      <div className="card">
        <h2>CV &amp; cover letter style</h2>
        <p className="muted">
          How your tailored CVs and cover letters look. Every style is plain, single-column text, so
          application systems read it cleanly. Previews use your latest tailored CV, or a sample until
          you have one. To change a CV that's already made, use the Style menu on its card.
        </p>
        <StyleGallery
          value={style}
          onChange={async (next) => {
            setStyle((await api.savePreferences({ document_style: next })).document_style);
          }}
        />
      </div>
    </section>
  );
}

// --- resume + application answers -------------------------------------------------------------

function ProfileSection() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [resume, setResume] = useState<DocumentInfo | null>(null);
  const [replacing, setReplacing] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api.getProfile().then(setProfile);
    api.listDocuments().then((docs) => setResume(docs.find((d) => d.is_base && d.kind === "resume") ?? null));
  }, []);

  return (
    <section id="profile" className="settings-section">
      <div className="card">
        <h2>Resume &amp; application answers</h2>
        <p className="muted">
          Your resume is the source for every tailored CV. Answers here fill application forms, so the
          agent asks you less.
        </p>
        {profile && (
          <>
            {replacing ? (
              <ResumeImport
                profile={profile}
                hasResume={false}
                onSaved={(p) => {
                  setProfile(p);
                  setReplacing(false);
                  api.listDocuments().then((docs) => setResume(docs.find((d) => d.is_base && d.kind === "resume") ?? null));
                  toast("Resume updated.");
                }}
              />
            ) : (
              <div className="row between" style={{ padding: "4px 0 14px" }}>
                <div className="row nowrap">
                  <Icon name="file" className="icon lg" />
                  <div>
                    <div className="strong">{resume?.original_filename ?? resume?.label ?? "No resume yet"}</div>
                    {resume && <div className="tiny faint">Uploaded {timeAgo(resume.updated_at)}</div>}
                  </div>
                </div>
                <div className="row" style={{ gap: 6 }}>
                  {resume?.has_file && (
                    <a className="btn btn-sm ghost" href={api.documentUrl(resume.id)} target="_blank" rel="noreferrer">
                      View
                    </a>
                  )}
                  <button className="sm" onClick={() => setReplacing(true)}>
                    Replace resume
                  </button>
                </div>
              </div>
            )}
            <hr className="divider" />
            <ApplicationAnswers profile={profile} onSaved={setProfile} />
          </>
        )}
      </div>
    </section>
  );
}

const ANSWER_FIELDS: { key: keyof ApplicationDefaults; label: string; placeholder: string }[] = [
  { key: "phone", label: "Phone", placeholder: "+1 416 555 0100" },
  { key: "address_line1", label: "Street address", placeholder: "123 Main St" },
  { key: "city", label: "City", placeholder: "Toronto" },
  { key: "province", label: "Province or state", placeholder: "ON" },
  { key: "postal_code", label: "Postal code", placeholder: "M5V 1A1" },
  { key: "country", label: "Country", placeholder: "Canada" },
  { key: "referral_source", label: "How did you hear about us?", placeholder: "LinkedIn" },
];

function ApplicationAnswers({ profile, onSaved }: { profile: Profile; onSaved: (p: Profile) => void }) {
  const [values, setValues] = useState<ApplicationDefaults>(profile.application_defaults);
  const { busy, run } = useAction();
  const saved = Object.entries(profile.application_defaults.saved_answers ?? {});
  const dirty = JSON.stringify(values) !== JSON.stringify(profile.application_defaults);
  const set = (key: keyof ApplicationDefaults, value: string | null) => setValues({ ...values, [key]: value });

  return (
    <div className="stack">
      <h3>Common form answers</h3>
      <div className="fields">
        {ANSWER_FIELDS.map((f) => (
          <div key={f.key} className="field">
            <label>{f.label}</label>
            <input
              value={(values[f.key] as string | null) ?? ""}
              placeholder={f.placeholder}
              onChange={(e) => set(f.key, e.target.value || null)}
            />
          </div>
        ))}
        <div className="field">
          <label>Willing to relocate?</label>
          <select
            value={values.willing_to_relocate ?? ""}
            onChange={(e) => set("willing_to_relocate", (e.target.value || null) as YesNo | null)}
          >
            <option value="">Not set</option>
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
        </div>
      </div>
      <div className="row">
        <button
          className="primary"
          disabled={!dirty || busy}
          onClick={() =>
            void run(async () => {
              const { saved_answers: _keep, ...patch } = values;
              onSaved(await api.updateProfile({ application_defaults: patch }));
            }, "Saved.")
          }
        >
          Save answers
        </button>
      </div>
      {saved.length > 0 && (
        <details>
          <summary>Answers you gave the agent ({saved.length})</summary>
          <dl className="answers">
            {saved.map(([q, a]) => (
              <div key={q} style={{ display: "contents" }}>
                <dt>{q}</dt>
                <dd>{a}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </div>
  );
}

// --- Claude ------------------------------------------------------------------------------------

function ClaudeSection() {
  const [status, setStatus] = useState<ClaudeStatus | null>(null);
  const [daily, setDaily] = useState("");
  const [monthly, setMonthly] = useState("");
  const { busy, run } = useAction();

  useEffect(() => {
    if (status) {
      setDaily(String(status.daily_budget_usd));
      setMonthly(String(status.monthly_budget_usd));
    }
  }, [status]);

  const budgetDirty =
    status != null &&
    (Number(daily) !== status.daily_budget_usd || Number(monthly) !== status.monthly_budget_usd);

  return (
    <section id="claude" className="settings-section">
      <div className="card">
        <h2>Claude</h2>
        <p className="muted">JobBee pays for Claude with your Anthropic key. Limits keep spending in check.</p>
        <ClaudeKey allowReplace onStatus={setStatus} />
        {status?.connected && (
          <>
            <div className="row" style={{ marginTop: 16 }}>
              <div className="stat">
                <span className="small muted">Today</span>
                <b>{usd(status.spend_today_usd)}</b>
              </div>
              <div className="stat">
                <span className="small muted">This month</span>
                <b>{usd(status.spend_month_usd)}</b>
              </div>
            </div>
            <div className="fields" style={{ marginTop: 16 }}>
              <div className="field">
                <label>Daily limit (USD)</label>
                <input type="number" min={0} step={1} value={daily} onChange={(e) => setDaily(e.target.value)} />
              </div>
              <div className="field">
                <label>Monthly limit (USD)</label>
                <input type="number" min={0} step={5} value={monthly} onChange={(e) => setMonthly(e.target.value)} />
              </div>
            </div>
            <div className="row" style={{ marginTop: 12 }}>
              <button
                className="primary"
                disabled={!budgetDirty || busy}
                onClick={() =>
                  void run(async () => {
                    setStatus(
                      await api.saveBudget({
                        daily_budget_usd: Number(daily),
                        monthly_budget_usd: Number(monthly),
                      }),
                    );
                  }, "Limits saved.")
                }
              >
                Save limits
              </button>
            </div>
            <details style={{ marginTop: 14 }}>
              <summary>Which Claude model does what</summary>
              <dl className="answers">
                {Object.entries(status.models).map(([task, model]) => (
                  <div key={task} style={{ display: "contents" }}>
                    <dt>{task}</dt>
                    <dd className="code" style={{ border: "none", borderRadius: 0 }}>
                      {model}
                    </dd>
                  </div>
                ))}
              </dl>
            </details>
          </>
        )}
      </div>
    </section>
  );
}

