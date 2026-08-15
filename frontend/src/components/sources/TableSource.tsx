import { useEffect, useState } from "react";

import { fetchTable } from "../../lib/sources";
import type { EvidenceItem, SqlLocator, TableContext } from "../../types";

/** What a SQL claim traces back to: what the table is, and what was asked of it. */
export function TableSource({ item }: { item: EvidenceItem }) {
  const locator = item.locator as SqlLocator;
  const [contexts, setContexts] = useState<TableContext[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setContexts(null);
    setError(null);
    Promise.all(
      locator.tables.map((table) => fetchTable(table, controller.signal))
    )
      .then(setContexts)
      .catch((reason: unknown) => {
        if (reason instanceof Error && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => controller.abort();
  }, [locator.tables]);

  return (
    <>
      <div className="source-meta">{locator.tables.join(", ")}</div>
      <pre className="source-sql">{locator.executed_sql}</pre>
      {error && <div className="source-state error">{error}</div>}
      {!contexts && !error && (
        <div className="source-state">Reading the reviewed context...</div>
      )}
      {contexts?.map((context) => (
        <section className="source-table" key={context.table}>
          <h3>{context.table}</h3>
          <p>{context.purpose}</p>
          <table className="source-columns">
            <tbody>
              {context.columns.map((column) => (
                <tr key={column.name}>
                  <th scope="row">{column.name}</th>
                  <td className="source-type">
                    {column.type}
                    {column.unit ? ` · ${column.unit}` : ""}
                  </td>
                  <td>{column.meaning}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {context.cautions.length > 0 && (
            <ul className="source-cautions">
              {context.cautions.map((caution) => (
                <li key={caution}>{caution}</li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </>
  );
}
