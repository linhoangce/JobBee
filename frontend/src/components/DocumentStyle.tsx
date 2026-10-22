import { useEffect, useRef, useState } from "react";
import { api, type DocumentStyle } from "../api";
import Icon from "./Icon";
import { useAction } from "./Toast";

const PAGE_WIDTH = 816; // 8.5in at 96dpi, what the preview page is laid out for
const PAGE_HEIGHT = 1056;

let stylesRequest: Promise<DocumentStyle[]> | null = null;

/** The CV styles, fetched once per page load. */
function useStyles(): DocumentStyle[] | null {
  const [styles, setStyles] = useState<DocumentStyle[] | null>(null);
  useEffect(() => {
    if (!stylesRequest) {
      stylesRequest = api.documentStyles().catch((e) => {
        stylesRequest = null;
        throw e;
      });
    }
    stylesRequest.then(setStyles).catch(() => setStyles(null));
  }, []);
  return styles;
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function Thumb({ style, kind }: { style: DocumentStyle; kind: "resume" | "cover_letter" }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const scale = width / PAGE_WIDTH;
  return (
    <div ref={ref} className="style-thumb">
      {width > 0 && (
        <iframe
          src={api.stylePreviewUrl(style.id, kind)}
          title={`${style.name} preview`}
          sandbox=""
          tabIndex={-1}
          aria-hidden="true"
          style={{ width: PAGE_WIDTH, height: PAGE_HEIGHT, transform: `scale(${scale})` }}
        />
      )}
    </div>
  );
}

/** Settings: pick the style every new CV and cover letter uses. */
export function StyleGallery({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (style: string) => Promise<void>;
}) {
  const styles = useStyles();
  const [kind, setKind] = useState<"resume" | "cover_letter">("resume");
  const { busy, run } = useAction();

  if (!styles) return <div className="skeleton" style={{ height: 260 }} />;

  return (
    <div className="stack">
      <div className="segmented" role="tablist" aria-label="Preview" style={{ alignSelf: "flex-start" }}>
        <button className={kind === "resume" ? "on" : ""} onClick={() => setKind("resume")}>
          CV
        </button>
        <button className={kind === "cover_letter" ? "on" : ""} onClick={() => setKind("cover_letter")}>
          Cover letter
        </button>
      </div>
      <div className="style-grid" role="radiogroup" aria-label="CV style">
        {styles.map((s) => {
          const chosen = s.id === value;
          return (
            <div key={s.id} className={`style-tile ${chosen ? "on" : ""}`}>
              <button
                className="style-pick"
                role="radio"
                aria-checked={chosen}
                disabled={busy}
                onClick={() => {
                  if (!chosen) void run(() => onChange(s.id), `New CVs will use the ${s.name} style.`);
                }}
              >
                <Thumb style={s} kind={kind} />
                <span className="style-name">
                  {s.name}
                  {chosen && <Icon name="check" />}
                </span>
                <span className="tiny muted style-desc">{s.description}</span>
              </button>
              <a className="tiny style-open" href={api.stylePreviewUrl(s.id, kind)} target="_blank" rel="noreferrer">
                Full size <Icon name="external" />
              </a>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** On a card or in the drawer: render this job's CV and letter in another style. */
export function StyleSwitch({
  applicationId,
  style,
  onChanged,
}: {
  applicationId: number;
  style: string | null;
  onChanged: () => void;
}) {
  const styles = useStyles();
  const { busy, run } = useAction();

  if (!styles) return null;
  const current = style ?? "";

  return (
    <label className="style-switch">
      <span className="tiny muted">Style</span>
      {busy && <span className="spinner" />}
      <select
        value={current}
        disabled={busy}
        aria-label="CV style for this job"
        onChange={(e) => {
          const next = e.target.value;
          void run(
            () => api.restyle(applicationId, next),
            (r) => (r as { message: string }).message,
          ).then((ok) => ok && onChanged());
        }}
      >
        {!current && <option value="">Original</option>}
        {styles.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
    </label>
  );
}
