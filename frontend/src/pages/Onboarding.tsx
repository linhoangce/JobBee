import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api,
  type ApplicationDefaults,
  type EmailStatus,
  type Profile,
  type ProfileDraft,
  type ProfileImportResponse,
  type WorkAuthorization,
  type WorkMode,
  type YesNo,
} from "../api";
import GmailConnect from "../components/GmailConnect";

type Step = "import" | "review";

const WORK_MODES: { value: WorkMode; label: string }[] = [
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "On-site" },
];

const WORK_AUTH_OPTIONS: { value: WorkAuthorization; label: string }[] = [
  { value: "yes_no_restrictions", label: "Yes, with no restrictions" },
  { value: "yes_with_restrictions", label: "Yes, with restrictions" },
  { value: "no", label: "No" },
];

const YES_NO_OPTIONS: { value: YesNo; label: string }[] = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
];

const DEFAULT_APPLICATION_DEFAULTS: ApplicationDefaults = {
  phone: null,
  phone_extension: null,
  phone_device_type: "Mobile",
  country_phone_code: null,
  address_line1: null,
  address_line2: null,
  city: null,
  province: null,
  postal_code: null,
  country: null,
  work_country: null,
  work_authorization: "yes_no_restrictions",
  requires_sponsorship: "no",
  referral_source: "LinkedIn Job Posting",
  willing_to_relocate: null,
  saved_answers: {},
};

function parseCsv(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * The onboarding wizard. Two steps:
 *   1. Import — drop a resume and/or enter a GitHub username.
 *   2. Review — every field is pre-filled and tagged "from <source>";
 *      the user just edits anything they want to change.
 *
 * "Skip and fill manually" jumps to Review with empty values.
 */
export default function Onboarding() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("import");

  const [profile, setProfile] = useState<Profile | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [emailStatus, setEmailStatus] = useState<EmailStatus | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const p = await api.getProfile();
        setProfile(p);
        // If the user previously filled the profile, start them on Review
        // with their existing data so this page is also a profile editor.
        if (p.name || p.headline || p.target_roles.length || p.onboarded_at) {
          setStep("review");
        }
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : String(e));
      }
    })();
    api.getEmailStatus().then(setEmailStatus).catch(() => setEmailStatus(null));
  }, []);

  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [sources, setSources] = useState<Record<string, string>>({});

  // Hydrate the draft from an existing profile when the user is returning
  // to edit (not coming through an import).
  useEffect(() => {
    if (profile && draft === null && step === "review") {
      setDraft(profileToDraft(profile));
      setSources({});
    }
  }, [profile, draft, step]);

  if (loadError) return <div className="notice bad">{loadError}</div>;
  if (!profile) return <div className="spinner">Loading…</div>;

  return (
    <>
      <h1>{step === "import" ? "Welcome to JobBee" : "Review your profile"}</h1>
      <p>
        {step === "import"
          ? "Drop your resume and / or your GitHub handle, and JobBee will fill in the rest. You can edit anything before you finish."
          : "Everything below is editable. Fields imported from a source show a small label so you know where the value came from."}
      </p>

      <ol className="stepper">
        <li
          className={`stepper-item ${step === "import" ? "active" : "done"}`}
          onClick={() => setStep("import")}
        >
          <span className="stepper-num">1</span>
          <span className="stepper-label">Import</span>
        </li>
        <li className={`stepper-item ${step === "review" ? "active" : ""}`}>
          <span className="stepper-num">2</span>
          <span className="stepper-label">Review &amp; finish</span>
        </li>
      </ol>

      {step === "import" ? (
        <ImportStep
          onImported={(result) => {
            setDraft(mergeIntoExistingDraft(result.draft, profileToDraft(profile)));
            setSources(result.sources);
            setStep("review");
          }}
          onSkip={() => {
            setDraft(profileToDraft(profile));
            setSources({});
            setStep("review");
          }}
        />
      ) : (
        <ReviewStep
          draft={draft ?? profileToDraft(profile)}
          sources={sources}
          emailStatus={emailStatus}
          onEmailStatusChange={() => api.getEmailStatus().then(setEmailStatus)}
          onChange={setDraft}
          onBack={() => setStep("import")}
          onFinish={async () => {
            const payload = draftToUpdate(draft ?? profileToDraft(profile));
            const updated = await api.updateProfile({ ...payload, mark_onboarded: true });
            setProfile(updated);
            navigate("/");
          }}
        />
      )}
    </>
  );
}

// --- Step 1: Import ---------------------------------------------------------

function ImportStep(props: {
  onImported: (result: ProfileImportResponse) => void;
  onSkip: () => void;
}) {
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [githubUsername, setGithubUsername] = useState("");
  const [githubToken, setGithubToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const canImport = (resumeFile !== null || githubUsername.trim().length > 0) && !importing;

  const onPickFile = (f: File | null) => {
    setError(null);
    setResumeFile(f);
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) onPickFile(file);
  };

  const onImport = async () => {
    setError(null);
    setImporting(true);
    try {
      const result = await api.importProfile({
        resumeFile: resumeFile ?? undefined,
        githubUsername: githubUsername.trim() || undefined,
        githubToken: githubToken.trim() || undefined,
      });
      props.onImported(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setImporting(false);
    }
  };

  return (
    <>
      <div className="card">
        <h2>Resume</h2>
        <p>PDF, DOCX, TXT, or Markdown. Used to pre-fill your name, headline, skills, and more.</p>
        <div
          className={`dropzone ${dragging ? "dragging" : ""} ${resumeFile ? "has-file" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => fileRef.current?.click()}
        >
          {resumeFile ? (
            <div className="dropzone-file">
              <div>
                <div className="doc-name">{resumeFile.name}</div>
                <div className="muted small">
                  {Math.round(resumeFile.size / 1024)} KB · {resumeFile.type || "unknown type"}
                </div>
              </div>
              <button
                className="secondary"
                onClick={(e) => {
                  e.stopPropagation();
                  onPickFile(null);
                }}
              >
                Replace
              </button>
            </div>
          ) : (
            <>
              <div className="dropzone-title">Drop your resume here</div>
              <div className="muted small">or click to choose a file (.pdf, .docx, .txt, .md)</div>
            </>
          )}
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.txt,.md,.markdown"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              if (f) onPickFile(f);
              e.target.value = "";
            }}
          />
        </div>
      </div>

      <div className="card">
        <h2>GitHub</h2>
        <p>We'll read your public profile and top repos to infer location, links, and skills.</p>
        <div className="field-row">
          <div className="field">
            <label>Username</label>
            <input
              type="text"
              value={githubUsername}
              onChange={(e) => setGithubUsername(e.target.value)}
              placeholder="octocat"
            />
          </div>
          <div className="field">
            <label>
              Token <span className="muted small">· optional, higher rate limits</span>
            </label>
            <input
              type={showToken ? "text" : "password"}
              value={githubToken}
              onChange={(e) => setGithubToken(e.target.value)}
              placeholder="ghp_…"
              autoComplete="off"
              onFocus={() => setShowToken(false)}
            />
          </div>
        </div>
      </div>

      {error && <div className="notice bad">{error}</div>}

      <div className="button-row">
        <button onClick={onImport} disabled={!canImport}>
          {importing ? "Importing…" : "Import & continue"}
        </button>
        <button className="secondary" onClick={props.onSkip} disabled={importing}>
          Skip and fill manually
        </button>
      </div>

      <p className="muted small" style={{ marginTop: 12 }}>
        Tip: import once, edit anything that's wrong on the next screen, then hit Finish.
        You can always come back here to re-import.
      </p>
    </>
  );
}

// --- Step 2: Review ---------------------------------------------------------

function ReviewStep(props: {
  draft: ProfileDraft;
  sources: Record<string, string>;
  emailStatus: EmailStatus | null;
  onEmailStatusChange: () => void | Promise<void>;
  onChange: (next: ProfileDraft) => void;
  onBack: () => void;
  onFinish: () => Promise<void>;
}) {
  const { draft, sources } = props;
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // We keep CSV-string mirrors for list inputs so the user can comma-edit.
  const [targetRolesCsv, setTargetRolesCsv] = useState(draft.target_roles.join(", "));
  const [targetLocationsCsv, setTargetLocationsCsv] = useState(draft.target_locations.join(", "));
  const [skillsCsv, setSkillsCsv] = useState(draft.skills.join(", "));

  // Re-sync the CSV mirrors if the draft is replaced (e.g. after a re-import).
  useEffect(() => {
    setTargetRolesCsv(draft.target_roles.join(", "));
    setTargetLocationsCsv(draft.target_locations.join(", "));
    setSkillsCsv(draft.skills.join(", "));
  }, [draft]);

  const set = <K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) => {
    props.onChange({ ...draft, [key]: value });
  };

  const toggleMode = (mode: WorkMode) => {
    const next = draft.work_modes.includes(mode)
      ? draft.work_modes.filter((m) => m !== mode)
      : [...draft.work_modes, mode];
    set("work_modes", next);
  };

  const setLink = (key: string, value: string) => {
    const next = { ...draft.links };
    if (value.trim()) next[key] = value.trim();
    else delete next[key];
    set("links", next);
  };

  const setAppDefault = <K extends keyof ApplicationDefaults>(key: K, value: ApplicationDefaults[K]) => {
    set("application_defaults", { ...draft.application_defaults, [key]: value });
  };

  const onFinish = async () => {
    setSaving(true);
    setError(null);
    try {
      // Flush CSV mirrors into the draft right before submit.
      const flushed: ProfileDraft = {
        ...draft,
        target_roles: parseCsv(targetRolesCsv),
        target_locations: parseCsv(targetLocationsCsv),
        skills: parseCsv(skillsCsv),
      };
      props.onChange(flushed);
      await props.onFinish();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const hasAnyImport = Object.keys(sources).length > 0;

  return (
    <>
      {hasAnyImport && (
        <div className="notice ok">
          Pre-filled from import. Edit anything that's off, then Finish.
        </div>
      )}

      <div className="card">
        <h2>About you</h2>
        <div className="field-row">
          <LabeledField label="Name" source={sources.name}>
            <input
              type="text"
              value={draft.name ?? ""}
              onChange={(e) => set("name", e.target.value)}
              placeholder="Jane Doe"
            />
          </LabeledField>
          <LabeledField label="Email" source={sources.email}>
            <input
              type="text"
              value={draft.email ?? ""}
              onChange={(e) => set("email", e.target.value)}
              placeholder="jane@example.com"
            />
          </LabeledField>
        </div>
        <LabeledField label="Headline" source={sources.headline}>
          <input
            type="text"
            value={draft.headline ?? ""}
            onChange={(e) => set("headline", e.target.value)}
            placeholder="Senior software engineer · platform"
          />
        </LabeledField>
        <div className="field-row">
          <LabeledField label="Current location" source={sources.location}>
            <input
              type="text"
              value={draft.location ?? ""}
              onChange={(e) => set("location", e.target.value)}
              placeholder="Toronto, ON"
            />
          </LabeledField>
          <LabeledField label="Years of experience" source={sources.years_experience}>
            <input
              type="number"
              min={0}
              step={0.5}
              value={draft.years_experience ?? ""}
              onChange={(e) =>
                set("years_experience", e.target.value ? Number(e.target.value) : null)
              }
              placeholder="6"
            />
          </LabeledField>
        </div>
        <LabeledField label="Summary" source={sources.summary}>
          <textarea
            rows={4}
            value={draft.summary ?? ""}
            onChange={(e) => set("summary", e.target.value)}
            placeholder="One paragraph: what you do, what you're great at, what you want next."
          />
        </LabeledField>
      </div>

      <div className="card">
        <h2>What you're looking for</h2>
        <LabeledField
          label="Target roles (comma separated)"
          source={sources.target_roles}
        >
          <input
            type="text"
            value={targetRolesCsv}
            onChange={(e) => setTargetRolesCsv(e.target.value)}
            onBlur={() => set("target_roles", parseCsv(targetRolesCsv))}
            placeholder="Software engineer, Backend engineer, Platform engineer"
          />
        </LabeledField>
        <LabeledField
          label="Target locations (comma separated)"
          source={sources.target_locations}
        >
          <input
            type="text"
            value={targetLocationsCsv}
            onChange={(e) => setTargetLocationsCsv(e.target.value)}
            onBlur={() => set("target_locations", parseCsv(targetLocationsCsv))}
            placeholder="Toronto, Remote (Canada), Remote (US)"
          />
        </LabeledField>
        <LabeledField label="Work modes" source={sources.work_modes}>
          <div className="chip-row">
            {WORK_MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                className={`chip ${draft.work_modes.includes(m.value) ? "active" : ""}`}
                onClick={() => toggleMode(m.value)}
              >
                {m.label}
              </button>
            ))}
          </div>
        </LabeledField>
        <div className="field-row">
          <LabeledField label="Minimum salary (USD/yr)" source={sources.min_salary}>
            <input
              type="number"
              min={0}
              step={1000}
              value={draft.min_salary ?? ""}
              onChange={(e) =>
                set("min_salary", e.target.value ? Number(e.target.value) : null)
              }
              placeholder="120000"
            />
          </LabeledField>
          <LabeledField label="Skills (comma separated)" source={sources.skills}>
            <input
              type="text"
              value={skillsCsv}
              onChange={(e) => setSkillsCsv(e.target.value)}
              onBlur={() => set("skills", parseCsv(skillsCsv))}
              placeholder="python, typescript, postgres, llm"
            />
          </LabeledField>
        </div>
      </div>

      <div className="card">
        <h2>Links</h2>
        <LabeledField label="LinkedIn" source={sources.links}>
          <input
            type="text"
            value={draft.links.linkedin ?? ""}
            onChange={(e) => setLink("linkedin", e.target.value)}
            placeholder="https://www.linkedin.com/in/yourname/"
          />
        </LabeledField>
        <div className="field-row">
          <LabeledField label="GitHub" source={sources.links}>
            <input
              type="text"
              value={draft.links.github ?? ""}
              onChange={(e) => setLink("github", e.target.value)}
              placeholder="https://github.com/yourname"
            />
          </LabeledField>
          <LabeledField label="Portfolio / website" source={sources.links}>
            <input
              type="text"
              value={draft.links.portfolio ?? ""}
              onChange={(e) => setLink("portfolio", e.target.value)}
              placeholder="https://you.dev"
            />
          </LabeledField>
        </div>
      </div>

      <div className="card">
        <h2>Application preferences</h2>
        <p className="muted small">
          Reused when Playwright fills ATS screening questions (work authorization, sponsorship,
          referral source, etc.).
        </p>
        <div className="field-row">
          <LabeledField label="Phone">
            <input
              type="text"
              value={draft.application_defaults.phone ?? ""}
              onChange={(e) => setAppDefault("phone", e.target.value || null)}
              placeholder="+1 416 555 0100"
            />
          </LabeledField>
          <LabeledField label="Work country">
            <input
              type="text"
              value={draft.application_defaults.work_country ?? ""}
              onChange={(e) => setAppDefault("work_country", e.target.value || null)}
              placeholder="Canada"
            />
          </LabeledField>
        </div>
        <div className="field-row">
          <LabeledField label="Phone device type">
            <input
              type="text"
              value={draft.application_defaults.phone_device_type ?? ""}
              onChange={(e) => setAppDefault("phone_device_type", e.target.value || null)}
              placeholder="Mobile"
            />
          </LabeledField>
          <LabeledField label="Country phone code">
            <input
              type="text"
              value={draft.application_defaults.country_phone_code ?? ""}
              onChange={(e) => setAppDefault("country_phone_code", e.target.value || null)}
              placeholder="Canada (+1)"
            />
          </LabeledField>
        </div>
        <div className="field-row">
          <LabeledField label="Address line 1">
            <input
              type="text"
              value={draft.application_defaults.address_line1 ?? ""}
              onChange={(e) => setAppDefault("address_line1", e.target.value || null)}
              placeholder="123 Main St"
            />
          </LabeledField>
          <LabeledField label="Address line 2">
            <input
              type="text"
              value={draft.application_defaults.address_line2 ?? ""}
              onChange={(e) => setAppDefault("address_line2", e.target.value || null)}
              placeholder="Apt, suite, unit"
            />
          </LabeledField>
        </div>
        <div className="field-row">
          <LabeledField label="City">
            <input
              type="text"
              value={draft.application_defaults.city ?? ""}
              onChange={(e) => setAppDefault("city", e.target.value || null)}
              placeholder="Toronto"
            />
          </LabeledField>
          <LabeledField label="Province or state">
            <input
              type="text"
              value={draft.application_defaults.province ?? ""}
              onChange={(e) => setAppDefault("province", e.target.value || null)}
              placeholder="ON"
            />
          </LabeledField>
        </div>
        <div className="field-row">
          <LabeledField label="Postal code">
            <input
              type="text"
              value={draft.application_defaults.postal_code ?? ""}
              onChange={(e) => setAppDefault("postal_code", e.target.value || null)}
              placeholder="M5V 1A1"
            />
          </LabeledField>
          <LabeledField label="Country">
            <input
              type="text"
              value={draft.application_defaults.country ?? ""}
              onChange={(e) => setAppDefault("country", e.target.value || null)}
              placeholder="Canada"
            />
          </LabeledField>
        </div>
        <div className="field-row">
          <LabeledField label="Work authorization">
            <select
              value={draft.application_defaults.work_authorization ?? "yes_no_restrictions"}
              onChange={(e) =>
                setAppDefault("work_authorization", e.target.value as WorkAuthorization)
              }
            >
              {WORK_AUTH_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </LabeledField>
          <LabeledField label="Requires visa sponsorship">
            <select
              value={draft.application_defaults.requires_sponsorship ?? "no"}
              onChange={(e) => setAppDefault("requires_sponsorship", e.target.value as YesNo)}
            >
              {YES_NO_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </LabeledField>
        </div>
        <div className="field-row">
          <LabeledField label="Default referral source">
            <input
              type="text"
              value={draft.application_defaults.referral_source ?? ""}
              onChange={(e) => setAppDefault("referral_source", e.target.value || null)}
              placeholder="LinkedIn Job Posting"
            />
          </LabeledField>
          <LabeledField label="Willing to relocate">
            <select
              value={draft.application_defaults.willing_to_relocate ?? ""}
              onChange={(e) =>
                setAppDefault(
                  "willing_to_relocate",
                  e.target.value ? (e.target.value as YesNo) : null,
                )
              }
            >
              <option value="">Not set</option>
              {YES_NO_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </LabeledField>
        </div>
      </div>

      <div className="card">
        <h2>Job alert email</h2>
        <p className="muted">
          Optional — sign in with Google to pull LinkedIn and Indeed job alerts automatically.
        </p>
        <GmailConnect
          compact
          emailStatus={props.emailStatus}
          onStatusChange={props.onEmailStatusChange}
        />
      </div>

      {error && <div className="notice bad">{error}</div>}

      <div className="button-row">
        <button className="secondary" onClick={props.onBack} disabled={saving}>
          Back
        </button>
        <button onClick={onFinish} disabled={saving}>
          {saving ? "Saving…" : "Finish"}
        </button>
      </div>
    </>
  );
}

// --- Small bits ---

function LabeledField(props: { label: string; source?: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label>
        {props.label}
        {props.source && <SourcePill source={props.source} />}
      </label>
      {props.children}
    </div>
  );
}

function SourcePill({ source }: { source: string }) {
  // Compact: "resume (lin.pdf)" -> "resume"
  const short = source.split(" ")[0];
  return (
    <span className="source-pill" title={`Imported from ${source}`}>
      from {short}
    </span>
  );
}

// --- Helpers ---

function profileToDraft(p: Profile): ProfileDraft {
  return {
    name: p.name,
    email: p.email,
    headline: p.headline,
    location: p.location,
    years_experience: p.years_experience,
    min_salary: p.min_salary,
    summary: p.summary,
    target_roles: p.target_roles,
    target_locations: p.target_locations,
    work_modes: p.work_modes,
    skills: p.skills,
    links: p.links,
    application_defaults: {
      ...DEFAULT_APPLICATION_DEFAULTS,
      ...p.application_defaults,
      saved_answers: { ...p.application_defaults.saved_answers },
    },
  };
}

/**
 * Take a freshly-imported draft and any pre-existing profile values, and
 * produce one combined draft. Importer values win for scalars where the
 * existing profile is empty; otherwise we keep what the user already had so
 * a re-import doesn't blow away manual edits. Lists are unioned.
 */
function mergeIntoExistingDraft(imported: ProfileDraft, existing: ProfileDraft): ProfileDraft {
  const isBlank = (v: unknown): boolean =>
    v === null || v === undefined || (typeof v === "string" && !v.trim());

  const pickScalar = <T,>(importedVal: T | null | undefined, existingVal: T | null | undefined): T | null => {
    // Imported values win; keep existing only when import omitted the field (manual edits / re-import gap-fill).
    if (!isBlank(importedVal)) return importedVal as T;
    if (!isBlank(existingVal)) return existingVal as T;
    return null;
  };
  const unionList = (a: string[], b: string[]): string[] => {
    const seen = new Set(a.map((s) => s.toLowerCase()));
    const out = [...a];
    for (const item of b) {
      const key = item.toLowerCase();
      if (!seen.has(key)) {
        out.push(item);
        seen.add(key);
      }
    }
    return out;
  };

  return {
    name: pickScalar(imported.name, existing.name),
    email: pickScalar(imported.email, existing.email),
    headline: pickScalar(imported.headline, existing.headline),
    location: pickScalar(imported.location, existing.location),
    years_experience: pickScalar(imported.years_experience, existing.years_experience),
    min_salary: pickScalar(imported.min_salary, existing.min_salary),
    summary: pickScalar(imported.summary, existing.summary),
    target_roles: unionList(imported.target_roles, existing.target_roles),
    target_locations: unionList(imported.target_locations, existing.target_locations),
    work_modes:
      imported.work_modes.length > 0 ? imported.work_modes : existing.work_modes,
    skills: unionList(imported.skills, existing.skills),
    links: { ...existing.links, ...imported.links },
    application_defaults: coalesceAppDefaults(
      existing.application_defaults,
      imported.application_defaults,
    ),
  };
}

function coalesceAppDefaults(
  base: ApplicationDefaults,
  patch: ApplicationDefaults,
): ApplicationDefaults {
  return {
    phone: patch.phone ?? base.phone,
    phone_extension: patch.phone_extension ?? base.phone_extension,
    phone_device_type: patch.phone_device_type ?? base.phone_device_type,
    country_phone_code: patch.country_phone_code ?? base.country_phone_code,
    address_line1: patch.address_line1 ?? base.address_line1,
    address_line2: patch.address_line2 ?? base.address_line2,
    city: patch.city ?? base.city,
    province: patch.province ?? base.province,
    postal_code: patch.postal_code ?? base.postal_code,
    country: patch.country ?? base.country,
    work_country: patch.work_country ?? base.work_country,
    work_authorization: patch.work_authorization ?? base.work_authorization,
    requires_sponsorship: patch.requires_sponsorship ?? base.requires_sponsorship,
    referral_source: patch.referral_source ?? base.referral_source,
    willing_to_relocate: patch.willing_to_relocate ?? base.willing_to_relocate,
    saved_answers: { ...base.saved_answers, ...patch.saved_answers },
  };
}

function draftToUpdate(draft: ProfileDraft) {
  return {
    name: draft.name ?? "",
    email: draft.email ?? undefined,
    headline: draft.headline ?? undefined,
    location: draft.location ?? undefined,
    years_experience: draft.years_experience ?? undefined,
    min_salary: draft.min_salary ?? undefined,
    summary: draft.summary ?? undefined,
    target_roles: draft.target_roles,
    target_locations: draft.target_locations,
    work_modes: draft.work_modes,
    skills: draft.skills,
    links: draft.links,
    application_defaults: draft.application_defaults,
  };
}
