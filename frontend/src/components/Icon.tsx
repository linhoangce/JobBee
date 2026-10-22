/** Small inline icon set (stroke icons, 24px grid). */

const PATHS: Record<string, string> = {
  check: "M5 12.5l4.5 4.5L19 7.5",
  x: "M6 6l12 12M18 6L6 18",
  alert:
    "M12 8v5m0 3.5v.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
  info: "M12 11v5m0-8.5v.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
  external: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5",
  file: "M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5zM14 3v5h5M9 13h6M9 17h6",
  search: "M11 18a7 7 0 100-14 7 7 0 000 14zM20 20l-4-4",
  link: "M10 14a4 4 0 005.66 0l3-3a4 4 0 00-5.66-5.66l-1 1M14 10a4 4 0 00-5.66 0l-3 3a4 4 0 005.66 5.66l1-1",
  pause: "M9 5v14M15 5v14",
  play: "M7 5l12 7-12 7V5z",
  chevron: "M9 6l6 6-6 6",
  down: "M6 9l6 6 6-6",
  monitor: "M4 5h16v11H4zM9 20h6M12 16v4",
  question: "M9.1 9a3 3 0 015.8 1c0 2-3 3-3 3m0 3.5v.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
  pin: "M12 21s-7-6.2-7-11.5a7 7 0 0114 0C19 14.8 12 21 12 21zM12 12a2.5 2.5 0 100-5 2.5 2.5 0 000 5z",
  money: "M3 7h18v10H3zM12 15a3 3 0 100-6 3 3 0 000 6zM6 10v4M18 10v4",
  clock: "M12 7v5l3 2M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
  send: "M4 12l16-8-6 16-2.5-6.5L4 12z",
  upload: "M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z",
  key: "M15 7a4 4 0 11-3.9 4.9L4 19v2h3v-2h2v-2h2l1.1-1.1A4 4 0 0115 7z",
  phone: "M8 3h8a1 1 0 011 1v16a1 1 0 01-1 1H8a1 1 0 01-1-1V4a1 1 0 011-1zM11 18h2",
  mail: "M4 6h16v12H4zM4 7l8 6 8-6",
  building:
    "M5 21V4a1 1 0 011-1h8a1 1 0 011 1v17M15 9h3a1 1 0 011 1v11M9 7h2M9 11h2M9 15h2M3 21h18",
  trash: "M5 7h14M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  refresh: "M20 11a8 8 0 00-14.9-3.9M4 5v4h4M4 13a8 8 0 0014.9 3.9M20 19v-4h-4",
};

export default function Icon({ name, className = "icon" }: { name: string; className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}

/** The JobBee mark: a honeycomb cell. */
export function BeeMark({ className = "brand-mark" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <path d="M16 2.5l11.7 6.75v13.5L16 29.5 4.3 22.75V9.25z" fill="currentColor" opacity="0.18" />
      <path
        d="M16 2.5l11.7 6.75v13.5L16 29.5 4.3 22.75V9.25z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinejoin="round"
      />
      <path
        d="M11 13.5h10M10 17.5h12M11.5 21.5h9"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  );
}
