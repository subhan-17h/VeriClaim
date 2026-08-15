import { useState } from "react";

import { SOURCE_ICON } from "./icons";
import { PolicyEvidence } from "./evidence/PolicyEvidence";
import { ScannedEvidence } from "./evidence/ScannedEvidence";
import { SpreadsheetEvidence } from "./evidence/SpreadsheetEvidence";
import { SqlEvidence } from "./evidence/SqlEvidence";
import type { CitationReport, EvidenceItem, SourceType } from "../types";

// A locator means something different in each source, so one renderer cannot serve
// all four honestly.
const RENDERER = {
  policy: PolicyEvidence,
  sql: SqlEvidence,
  spreadsheet: SpreadsheetEvidence,
  scanned_pdf: ScannedEvidence
} as const;

/** Exposed for test: every source type must have a renderer. */
export function rendererName(source: SourceType): string {
  return RENDERER[source].name;
}

const GROUP_LABEL: Record<SourceType, string> = {
  policy: "Policy documents",
  sql: "Claims database",
  spreadsheet: "Operational spreadsheets",
  scanned_pdf: "Scanned documents"
};

const ORDER: SourceType[] = ["policy", "sql", "spreadsheet", "scanned_pdf"];

export function EvidenceCards({
  evidence,
  citations,
  onOpenSource
}: {
  evidence: EvidenceItem[];
  citations: CitationReport;
  onOpenSource: (item: EvidenceItem) => void;
}) {
  const [open, setOpen] = useState<SourceType | null>(null);
  if (evidence.length === 0) return null;

  const cited = new Set(citations.resolved);
  const groups = ORDER.map((source) => ({
    source,
    items: evidence.filter((item) => item.source_type === source)
  })).filter((group) => group.items.length > 0);

  return (
    <section className="evidence" aria-label="Evidence">
      <div className="evidence-tabs">
        {groups.map(({ source, items }) => {
          const Icon = SOURCE_ICON[source];
          const used = items.filter((item) => cited.has(item.id)).length;
          return (
            <button
              key={source}
              type="button"
              className={"evidence-tab" + (open === source ? " active" : "")}
              onClick={() => setOpen(open === source ? null : source)}
              aria-expanded={open === source}
            >
              <Icon />
              <span>{GROUP_LABEL[source]}</span>
              {/* cited/total, so evidence gathered and then ignored is visible
                  rather than implied. */}
              <span className="evidence-count">
                {used}/{items.length}
              </span>
            </button>
          );
        })}
      </div>

      {groups
        .filter((group) => group.source === open)
        .map(({ source, items }) => {
          const Renderer = RENDERER[source];
          return (
            <div className="evidence-panel" key={source}>
              {items.map((item) => (
                <article
                  className={"ev-card" + (cited.has(item.id) ? " cited" : "")}
                  id={`evidence-${item.id}`}
                  key={item.id}
                >
                  {/* The head carries the id and whether the answer used it. The
                      provenance line belongs to the renderer, which knows what this
                      source's locator actually means -- printing the backend's
                      citation string here too would say the same thing twice. */}
                  <div className="ev-head">
                    <span className="ev-id">[{item.id}]</span>
                    {!cited.has(item.id) && (
                      <span className="ev-uncited">not cited</span>
                    )}
                    <button
                      type="button"
                      className="ev-open"
                      onClick={() => onOpenSource(item)}
                    >
                      Open source
                    </button>
                  </div>
                  <Renderer item={item} />
                </article>
              ))}
            </div>
          );
        })}
    </section>
  );
}
