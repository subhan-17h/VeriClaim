import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { EvidenceCards } from "./EvidenceCard";
import { Ico } from "./icons";
import { Stages } from "./Stages";
import { withCitations } from "../lib/citations";
import type { Event, FinalEvent, StageEvent } from "../types";

/**
 * One question and everything the run reported about answering it.
 *
 * A turn owns its own stages, final payload and error, so a conversation restored
 * from history renders through exactly the same path as a live one -- there is no
 * second shape for replay.
 */
export type Turn = {
  id: string;
  question: string;
  stages: StageEvent[];
  final: FinalEvent | null;
  error: string | null;
  running: boolean;
};

export function turnFromEvent(turn: Turn, event: Event): Turn {
  if (event.event === "stage") return { ...turn, stages: [...turn.stages, event] };
  if (event.event === "final") return { ...turn, final: event, running: false };
  if (event.event === "error") return { ...turn, error: event.message, running: false };
  return turn;
}

function Verdict({ final }: { final: FinalEvent }) {
  const { verified, degraded } = final.citations;
  if (degraded) {
    return (
      <div className="verdict degraded">
        Not verified against the evidence. Shown for inspection only; no conclusion
        should be drawn from it.
      </div>
    );
  }
  if (!verified) {
    return (
      <div className="verdict unchecked">
        The answer's citations resolve, but it could not be checked further.
      </div>
    );
  }
  return null;
}

export function Message({ turn }: { turn: Turn }) {
  const final = turn.final;
  const evidence = final?.evidence ?? [];

  return (
    <>
      <div className="msg user">
        <div className="msg-avatar">Q</div>
        <div className="msg-col">
          <div className="bubble">{turn.question}</div>
        </div>
      </div>

      <div className="msg assistant">
        <div className="msg-avatar">
          <Ico.Spark className="spark" />
        </div>
        <div className="msg-col">
          <Stages stages={turn.stages} running={turn.running} />

          {final && (
            <>
              <Verdict final={final} />
              <div className="bubble">
                <div className="markdown">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      p: ({ children }) => <p>{withCitations(children, evidence)}</p>,
                      li: ({ children }) => <li>{withCitations(children, evidence)}</li>,
                      table: ({ children }) => (
                        <div className="markdown-table-wrap">
                          <table>{children}</table>
                        </div>
                      )
                    }}
                  >
                    {final.answer}
                  </ReactMarkdown>
                </div>
              </div>

              {final.failures.length > 0 && (
                <div className="api-error">
                  {final.failures.map((failure) => (
                    <div key={failure}>{failure}</div>
                  ))}
                </div>
              )}

              <EvidenceCards evidence={evidence} citations={final.citations} />
            </>
          )}

          {turn.error && <div className="api-error">{turn.error}</div>}
        </div>
      </div>
    </>
  );
}
