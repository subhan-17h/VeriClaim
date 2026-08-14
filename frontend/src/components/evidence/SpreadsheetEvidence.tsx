import type { EvidenceItem, SpreadsheetLocator } from "../../types";

export function SpreadsheetEvidence({ item }: { item: EvidenceItem }) {
  const locator = item.locator as SpreadsheetLocator;
  return (
    <>
      {/* workbook > sheet > row > A1 is the cell-level provenance the engineering
          invariants require of this source; nothing here is inferred. */}
      <div className="ev-meta">
        <span>{locator.workbook}</span>
        <span>{locator.sheet}</span>
        {locator.row !== null && <span>Row {locator.row}</span>}
        <span className="ev-strong">{locator.a1_range}</span>
      </div>
      <p className="ev-text">{item.content}</p>
    </>
  );
}
