import { useEffect } from "react";

import { PdfSource } from "./sources/PdfSource";
import { SheetSource } from "./sources/SheetSource";
import { TableSource } from "./sources/TableSource";
import type { EvidenceItem, SheetGrid, SourceType } from "../types";

// A locator means something different in each source, so opening one does too. This is
// the table the drawer actually renders through, not a second list kept for the test:
// a table only the test reads would let the body drift away from what it asserts.
const RENDERER: Record<
  SourceType,
  (props: { item: EvidenceItem }) => JSX.Element | null
> = {
  policy: PdfSource,
  scanned_pdf: PdfSource,
  spreadsheet: SheetSource,
  sql: TableSource
};

/** Exposed for test: every source type must be openable. */
export function sourceRendererName(source: SourceType): string {
  return RENDERER[source].name;
}

/** Exposed for test: which rendered row a locator's row number addresses. */
export function citedRowIndex(grid: SheetGrid, row: number | null): number | null {
  if (row === null) return null;
  const index = row - grid.first_row;
  return index >= 0 && index < grid.rows.length ? index : null;
}

function renderSource(item: EvidenceItem) {
  const Renderer = RENDERER[item.source_type];
  return <Renderer item={item} />;
}

export function SourceDrawer({
  item,
  onClose
}: {
  item: EvidenceItem | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!item) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, onClose]);

  if (!item) return null;

  return (
    <aside className="source-drawer" aria-label="Source">
      <div className="source-head">
        <span className="source-id">[{item.id}]</span>
        <span className="source-label">{item.source_label}</span>
        <button type="button" className="source-close" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="source-body">{renderSource(item)}</div>
    </aside>
  );
}
