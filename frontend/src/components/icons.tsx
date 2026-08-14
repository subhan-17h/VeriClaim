import type { SVGProps } from "react";

// Icons carry their own default size. An SVG with no width renders at its intrinsic
// size, which in a flex row means it swallows the label beside it -- so the default
// is set here rather than left to each call site to remember.
const S = (d: string) =>
  function Icon({ width = 16, height = 16, ...props }: SVGProps<SVGSVGElement>) {
    return (
      <svg
        viewBox="0 0 24 24"
        width={width}
        height={height}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        {...props}
      >
        <path d={d} />
      </svg>
    );
  };

export const Ico = {
  Plus: S("M12 5v14M5 12h14"),
  Send: S("M4 12l16-8-6 8 6 8z"),
  Sun: S(
    "M12 4v2M12 18v2M4 12h2M18 12h2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4M15 12a3 3 0 11-6 0 3 3 0 016 0z"
  ),
  Moon: S("M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"),
  Panel: S("M4 5h16v14H4zM10 5v14"),
  Trash: S("M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"),
  Check: S("M5 13l4 4 10-10"),
  Doc: S("M7 3h7l5 5v13H7zM14 3v5h5"),
  Db: S(
    "M5 7c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3zM5 7v10c0 1.7 3.1 3 7 3s7-1.3 7-3V7"
  ),
  Grid: S("M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"),
  Scan: S("M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4M7 12h10"),
  Spark: S("M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z")
};

/** The icon that stands for a source type, wherever evidence is labelled. */
export const SOURCE_ICON = {
  policy: Ico.Doc,
  sql: Ico.Db,
  spreadsheet: Ico.Grid,
  scanned_pdf: Ico.Scan
} as const;

// The claim sits inside the shield: evidence, protected. Adapted from CSRS's mark,
// whose document-in-shield says the same thing about a standard.
export function Logo(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 128 128" fill="none" aria-hidden="true" {...props}>
      <path
        d="M64 12 104 28v29c0 27-16 48-40 59-24-11-40-32-40-59V28l40-16Z"
        stroke="currentColor"
        strokeWidth="7"
        strokeLinejoin="round"
      />
      <path
        d="M45 62l13 13 25-25"
        stroke="currentColor"
        strokeWidth="7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
