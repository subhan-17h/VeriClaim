import type { EvidenceItem, SqlLocator } from "../../types";

export function SqlEvidence({ item }: { item: EvidenceItem }) {
  const locator = item.locator as SqlLocator;
  return (
    <>
      <div className="ev-meta">
        <span>{locator.tables.join(", ")}</span>
        <span>
          {locator.row_count} {locator.row_count === 1 ? "row" : "rows"}
        </span>
      </div>
      <p className="ev-text">{item.content}</p>
      {/* The executed SQL is exposed on purpose (C-9.3): a reader can check the
          number against the query that produced it. Neither reference repo does
          this, and it is the difference between a figure and a citation. */}
      <pre className="ev-sql">
        <code>{locator.executed_sql}</code>
      </pre>
    </>
  );
}
