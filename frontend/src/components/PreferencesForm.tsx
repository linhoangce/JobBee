import type { Preferences, WorkAuthorization, WorkMode, YesNo } from "../api";
import ChipInput from "./ChipInput";

const MODES: { value: WorkMode; label: string }[] = [
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "On-site" },
];

const AUTH: { value: WorkAuthorization; label: string }[] = [
  { value: "yes_no_restrictions", label: "Yes, no restrictions" },
  { value: "yes_with_restrictions", label: "Yes, with restrictions" },
  { value: "no", label: "No" },
];

const PER_DAY = [3, 5, 10, 15];

type Props = {
  value: Preferences;
  onChange: (next: Preferences) => void;
  roleSuggestions?: string[];
  locationSuggestions?: string[];
  /** Settings shows the finer controls too. */
  extended?: boolean;
};

/** What to look for. Shared by the welcome flow and Settings. */
export default function PreferencesForm({
  value,
  onChange,
  roleSuggestions = [],
  locationSuggestions = [],
  extended = false,
}: Props) {
  const set = <K extends keyof Preferences>(key: K, v: Preferences[K]) => onChange({ ...value, [key]: v });

  const toggleMode = (mode: WorkMode) =>
    set(
      "work_modes",
      value.work_modes.includes(mode)
        ? value.work_modes.filter((m) => m !== mode)
        : [...value.work_modes, mode],
    );

  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="field">
        <label>Roles</label>
        <ChipInput
          values={value.target_roles}
          onChange={(v) => set("target_roles", v)}
          placeholder="e.g. Backend Engineer"
          suggestions={roleSuggestions}
        />
      </div>

      <div className="field">
        <label>Where</label>
        <ChipInput
          values={value.target_locations}
          onChange={(v) => set("target_locations", v)}
          placeholder="e.g. Toronto, Vancouver"
          suggestions={locationSuggestions}
        />
        <div className="segmented" style={{ marginTop: 6, alignSelf: "flex-start" }}>
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              className={value.work_modes.includes(m.value) ? "on" : ""}
              aria-pressed={value.work_modes.includes(m.value)}
              onClick={() => toggleMode(m.value)}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      <div className="fields">
        <div className="field">
          <label>Minimum salary</label>
          <input
            type="number"
            min={0}
            step={5000}
            value={value.min_salary ?? ""}
            placeholder="e.g. 120000"
            onChange={(e) => set("min_salary", e.target.value ? Number(e.target.value) : null)}
          />
          <span className="hint">Per year. Leave empty if you're flexible.</span>
        </div>
        <div className="field">
          <label>Jobs to send you per day</label>
          <div className="segmented">
            {PER_DAY.map((n) => (
              <button
                key={n}
                type="button"
                className={value.jobs_per_day === n ? "on" : ""}
                onClick={() => set("jobs_per_day", n)}
              >
                {n}
              </button>
            ))}
          </div>
          <span className="hint">Only the best matches, each with a tailored CV.</span>
        </div>
        <div className="field">
          <label>Can you work there without a new visa?</label>
          <select
            value={value.work_authorization ?? ""}
            onChange={(e) =>
              set("work_authorization", (e.target.value || null) as WorkAuthorization | null)
            }
          >
            <option value="">Choose…</option>
            {AUTH.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Need visa sponsorship?</label>
          <div className="segmented">
            {(["no", "yes"] as YesNo[]).map((v) => (
              <button
                key={v}
                type="button"
                className={value.requires_sponsorship === v ? "on" : ""}
                onClick={() => set("requires_sponsorship", v)}
              >
                {v === "yes" ? "Yes" : "No"}
              </button>
            ))}
          </div>
        </div>
      </div>

      {extended && (
        <>
          <div className="fields">
            <div className="field">
              <label>
                Lowest match score to send: <span className="strong">{value.min_match_score}</span>
              </label>
              <input
                type="range"
                min={40}
                max={90}
                step={5}
                value={value.min_match_score}
                onChange={(e) => set("min_match_score", Number(e.target.value))}
                style={{ padding: 0, minHeight: 28, accentColor: "var(--accent)" }}
              />
              <span className="hint">Higher means fewer, better-fitting jobs.</span>
            </div>
            <div className="field">
              <label>Daily search time</label>
              <input
                type="time"
                value={value.discovery_time}
                onChange={(e) => set("discovery_time", e.target.value || "09:00")}
              />
              <span className="hint">If your PC is off then, it searches when it's back on.</span>
            </div>
            <div className="field">
              <label>Ignore postings older than</label>
              <select
                value={value.max_job_age_days}
                onChange={(e) => set("max_job_age_days", Number(e.target.value))}
              >
                {[7, 14, 30, 60].map((d) => (
                  <option key={d} value={d}>
                    {d} days
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="field">
            <label>Never send jobs from</label>
            <ChipInput
              values={value.excluded_companies}
              onChange={(v) => set("excluded_companies", v)}
              placeholder="Company names"
            />
          </div>
        </>
      )}
    </div>
  );
}
