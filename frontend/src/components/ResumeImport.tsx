import { useRef, useState } from "react";
import { api, type ApplicationDefaults, type Profile, type ProfileDraft, type ProfileUpdate } from "../api";
import { initials } from "../lib/format";
import Icon from "./Icon";
import { errorText } from "./Toast";

type Phase = "existing" | "pick" | "importing" | "review";

/** Drop a resume → Claude reads it → confirm the summary. Saves the profile on "Looks right". */
export default function ResumeImport({
  profile,
  hasResume,
  onSaved,
}: {
  profile: Profile;
  hasResume: boolean;
  onSaved: (profile: Profile) => void;
}) {
  const [phase, setPhase] = useState<Phase>(hasResume && profile.name ? "existing" : "pick");
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const importFile = async (file: File) => {
    setError(null);
    setFileName(file.name);
    setPhase("importing");
    try {
      const result = await api.importProfile(file);
      setDraft(mergeDraft(profile, result.draft));
      setWarnings(result.warnings);
      setPhase("review");
    } catch (e) {
      setError(errorText(e));
      setPhase("pick");
    }
  };

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.updateProfile(toUpdate(draft)));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  if (phase === "existing") {
    return (
      <div className="stack">
        <ProfileSummary draft={profileToDraft(profile)} />
        <div className="row">
          <button className="primary lg" onClick={() => onSaved(profile)}>
            Continue with this
          </button>
          <button className="ghost" onClick={() => setPhase("pick")}>
            Use a different resume
          </button>
        </div>
      </div>
    );
  }

  if (phase === "pick" || phase === "importing") {
    return (
      <div className="stack">
        <div
          className={`dropzone ${dragging ? "dragging" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => phase === "pick" && fileRef.current?.click()}
          onKeyDown={(e) => e.key === "Enter" && fileRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const file = e.dataTransfer.files?.[0];
            if (file && phase === "pick") void importFile(file);
          }}
        >
          {phase === "importing" ? (
            <>
              <div className="spinner" style={{ width: 28, height: 28, borderWidth: 3 }} />
              <div className="strong">Reading {fileName}…</div>
              <div className="small muted">Claude is pulling out your experience and skills.</div>
            </>
          ) : (
            <>
              <Icon name="upload" />
              <div className="strong">Drop your resume here</div>
              <div className="small muted">or click to choose · PDF or Word works best</div>
            </>
          )}
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.txt,.md"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void importFile(f);
              e.target.value = "";
            }}
          />
        </div>
        {error && (
          <div className="notice bad small">
            <Icon name="alert" />
            {error}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="stack">
      {draft && !editing && <ProfileSummary draft={draft} />}
      {draft && editing && <DetailsForm draft={draft} onChange={setDraft} />}
      {warnings.map((w) => (
        <div key={w} className="notice warn small">
          <Icon name="alert" />
          {w}
        </div>
      ))}
      {error && (
        <div className="notice bad small">
          <Icon name="alert" />
          {error}
        </div>
      )}
      <div className="row">
        <button className="primary lg" disabled={saving} onClick={() => void save()}>
          {saving ? "Saving…" : editing ? "Save" : "Looks right"}
        </button>
        {!editing && (
          <button className="ghost" onClick={() => setEditing(true)}>
            Fix something
          </button>
        )}
        <button className="ghost" onClick={() => setPhase("pick")}>
          Different file
        </button>
      </div>
    </div>
  );
}

function ProfileSummary({ draft }: { draft: ProfileDraft }) {
  const facts = [
    draft.location,
    draft.years_experience != null ? `${draft.years_experience} years experience` : null,
    draft.email,
  ].filter(Boolean);
  return (
    <div className="card">
      <div className="profile-summary">
        <div className="avatar">{initials(draft.name)}</div>
        <div className="grow">
          <div className="strong" style={{ fontSize: 17 }}>
            {draft.name || "Your name"}
          </div>
          {draft.headline && <div className="muted">{draft.headline}</div>}
          {facts.length > 0 && <div className="small faint" style={{ marginTop: 2 }}>{facts.join(" · ")}</div>}
        </div>
      </div>
      {draft.skills.length > 0 && (
        <div className="skill-cloud">
          {draft.skills.slice(0, 14).map((s) => (
            <span key={s} className="tag">
              {s}
            </span>
          ))}
          {draft.skills.length > 14 && <span className="tag">+{draft.skills.length - 14}</span>}
        </div>
      )}
    </div>
  );
}

function DetailsForm({ draft, onChange }: { draft: ProfileDraft; onChange: (d: ProfileDraft) => void }) {
  const set = <K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) => onChange({ ...draft, [key]: value });
  return (
    <div className="card fields">
      <div className="field">
        <label>Name</label>
        <input value={draft.name ?? ""} onChange={(e) => set("name", e.target.value)} />
      </div>
      <div className="field">
        <label>Email</label>
        <input value={draft.email ?? ""} onChange={(e) => set("email", e.target.value)} />
      </div>
      <div className="field full">
        <label>Headline</label>
        <input value={draft.headline ?? ""} onChange={(e) => set("headline", e.target.value)} />
      </div>
      <div className="field">
        <label>Where you live</label>
        <input value={draft.location ?? ""} onChange={(e) => set("location", e.target.value)} />
      </div>
      <div className="field">
        <label>Years of experience</label>
        <input
          type="number"
          min={0}
          step={0.5}
          value={draft.years_experience ?? ""}
          onChange={(e) => set("years_experience", e.target.value ? Number(e.target.value) : null)}
        />
      </div>
      <div className="field full">
        <label>Phone (for application forms)</label>
        <input
          value={draft.application_defaults.phone ?? ""}
          onChange={(e) =>
            set("application_defaults", { ...draft.application_defaults, phone: e.target.value || null })
          }
        />
      </div>
    </div>
  );
}

// --- helpers --------------------------------------------------------------------------

export function profileToDraft(p: Profile): ProfileDraft {
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
    application_defaults: p.application_defaults,
  };
}

function union(a: string[], b: string[]): string[] {
  const seen = new Set(a.map((s) => s.toLowerCase()));
  return [...a, ...b.filter((s) => !seen.has(s.toLowerCase()))];
}

/** Imported values win for scalars; lists are merged; existing answers are kept. */
function mergeDraft(profile: Profile, imported: ProfileDraft): ProfileDraft {
  const existing = profileToDraft(profile);
  const pick = <T,>(a: T | null | undefined, b: T | null | undefined): T | null =>
    a !== null && a !== undefined && a !== "" ? a : (b ?? null);
  const defaults: ApplicationDefaults = { ...existing.application_defaults };
  for (const [k, v] of Object.entries(imported.application_defaults ?? {})) {
    if (v !== null && v !== undefined && k !== "saved_answers") {
      (defaults as Record<string, unknown>)[k] = v;
    }
  }
  return {
    name: pick(imported.name, existing.name),
    email: pick(imported.email, existing.email),
    headline: pick(imported.headline, existing.headline),
    location: pick(imported.location, existing.location),
    years_experience: pick(imported.years_experience, existing.years_experience),
    min_salary: pick(existing.min_salary, imported.min_salary),
    summary: pick(imported.summary, existing.summary),
    target_roles: union(existing.target_roles, imported.target_roles),
    target_locations: union(existing.target_locations, imported.target_locations),
    work_modes: existing.work_modes.length ? existing.work_modes : imported.work_modes,
    skills: union(imported.skills, existing.skills),
    links: { ...existing.links, ...imported.links },
    application_defaults: defaults,
  };
}

function toUpdate(d: ProfileDraft): ProfileUpdate {
  const { saved_answers: _ignored, ...defaults } = d.application_defaults;
  return {
    name: d.name ?? "",
    email: d.email || undefined,
    headline: d.headline || undefined,
    location: d.location || undefined,
    years_experience: d.years_experience ?? undefined,
    summary: d.summary || undefined,
    target_roles: d.target_roles,
    target_locations: d.target_locations,
    work_modes: d.work_modes,
    skills: d.skills,
    links: d.links,
    application_defaults: defaults,
  };
}
