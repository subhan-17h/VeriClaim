import { useEffect, useState } from "react";

import { Ico } from "./icons";
import type { StageEvent } from "../types";

/** What a stage is called in the interface. Anything unlisted shows its own name. */
const LABEL: Record<string, string> = {
  understand: "Reading the question",
  route: "Choosing sources",
  plan: "Planning the work",
  collect: "Collecting evidence",
  sufficiency: "Checking for gaps",
  synthesize: "Writing the answer",
  verify: "Verifying citations"
};

const SOURCE_LABEL: Record<string, string> = {
  policy: "policy documents",
  sql: "the claims database",
  spreadsheet: "the spreadsheets",
  scanned_pdf: "scanned documents"
};

function label(name: string): string {
  if (name.startsWith("source.")) {
    const source = name.slice("source.".length);
    return `Querying ${SOURCE_LABEL[source] ?? source}`;
  }
  return LABEL[name] ?? name;
}

export function Stages({
  stages,
  running
}: {
  stages: StageEvent[];
  running: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [, setTick] = useState(0);

  // Re-render while a run is in flight so the elapsed figures move rather than
  // sitting frozen at whatever they were when the last event landed.
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setTick((n) => n + 1), 120);
    return () => window.clearInterval(timer);
  }, [running]);

  if (stages.length === 0 && !running) return null;

  const total = stages.reduce((sum, stage) => sum + stage.latency_ms, 0);
  const failed = stages.filter((stage) => stage.error).length;

  // While running the list is the interesting thing; once finished it collapses to
  // one line, because a finished pipeline is history and the answer is the point.
  if (!running && !expanded) {
    return (
      <button
        type="button"
        className="stage-summary"
        onClick={() => setExpanded(true)}
      >
        <Ico.Check />
        <span>
          {stages.length} steps in {(total / 1000).toFixed(1)}s
          {failed > 0 ? `, ${failed} failed` : ""}
        </span>
      </button>
    );
  }

  return (
    <div className="stage-list">
      {stages.map((stage, index) => (
        <div
          className={"stage-row" + (stage.error ? " failed" : "")}
          key={`${stage.name}-${index}`}
        >
          <span className="stage-dot" aria-hidden="true" />
          <span className="stage-name">{label(stage.name)}</span>
          {stage.error ? (
            <span className="stage-error" title={stage.error}>
              {stage.error}
            </span>
          ) : (
            <span className="stage-time">
              {(stage.latency_ms / 1000).toFixed(1)}s
            </span>
          )}
        </div>
      ))}
      {running && (
        <div className="stage-row">
          <span className="stage-dot pulse" aria-hidden="true" />
          <span className="stage-name">Working...</span>
        </div>
      )}
      {!running && (
        <button
          type="button"
          className="stage-collapse"
          onClick={() => setExpanded(false)}
        >
          Hide steps
        </button>
      )}
    </div>
  );
}
