import { useEffect, useRef, useState } from "react";

import { fetchSheet } from "../../lib/sources";
import { citedRowIndex } from "../SourceDrawer";
import type { EvidenceItem, SheetGrid, SpreadsheetLocator } from "../../types";

// Every renderer takes the whole item and reads the locator it knows, so one dispatch
// table can serve all four -- the same shape EvidenceCard uses.
export function SheetSource({ item }: { item: EvidenceItem }) {
  const locator = item.locator as SpreadsheetLocator;
  const [grid, setGrid] = useState<SheetGrid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const citedRef = useRef<HTMLTableRowElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setGrid(null);
    setError(null);
    fetchSheet(locator.workbook, locator.sheet, controller.signal)
      .then(setGrid)
      .catch((reason: unknown) => {
        if (reason instanceof Error && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => controller.abort();
  }, [locator.workbook, locator.sheet]);

  useEffect(() => {
    citedRef.current?.scrollIntoView({ block: "center" });
  }, [grid]);

  if (error) return <div className="source-state error">{error}</div>;
  if (!grid) return <div className="source-state">Opening the workbook...</div>;

  const cited = citedRowIndex(grid, locator.row);

  return (
    <>
      <div className="source-meta">
        {grid.workbook} › {grid.sheet}
        {locator.row === null ? "" : ` › row ${locator.row}`}
        {locator.a1_range ? ` › ${locator.a1_range}` : ""}
      </div>
      {grid.truncated && (
        <div className="source-state">
          Showing the first {grid.rows.length} of {grid.total_rows} rows.
        </div>
      )}
      <div className="source-grid-wrap">
        <table className="source-grid">
          <thead>
            <tr>
              <th />
              {grid.columns.map((letter) => (
                <th key={letter}>{letter}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.rows.map((row, index) => (
              <tr
                key={grid.first_row + index}
                className={index === cited ? "cited" : ""}
                ref={index === cited ? citedRef : null}
              >
                <th scope="row">{grid.first_row + index}</th>
                {row.map((cell, column) => (
                  <td key={grid.columns[column] ?? column}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

