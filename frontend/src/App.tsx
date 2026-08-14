import { useState } from "react";

import { askStream } from "./lib/api";
import type { Event } from "./types";

const DEFAULT_QUESTION = "Are burst pipes covered under HomeSecure?";

function describe(event: Event): string {
  switch (event.event) {
    case "run_started":
      return event.trace_id;
    case "stage":
      return `${event.name}${event.error ? ` -- ${event.error}` : ""}`;
    case "evidence":
      return `${event.source}: ${event.items.length}`;
    case "final":
      return `${event.citations.resolved.length} citations, verified=${event.citations.verified}`;
    case "error":
      return event.message;
  }
}

export default function App() {
  const [question, setQuestion] = useState(DEFAULT_QUESTION);
  const [events, setEvents] = useState<Event[]>([]);
  const [status, setStatus] = useState("idle");

  const run = async () => {
    setEvents([]);
    setStatus("running");
    try {
      const final = await askStream(question, (event) =>
        setEvents((seen) => [...seen, event])
      );
      setStatus(`done -- ${final.evidence.length} evidence items`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <main className="shell">
      <h1>VeriClaim</h1>
      <div className="row">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          aria-label="Question"
        />
        <button onClick={run} disabled={status === "running"}>
          Ask
        </button>
      </div>
      <p className="status">{status}</p>
      <div className="frames">
        {events.map((event, index) => (
          <div className="frame" key={index}>
            <span className="name">{event.event}</span> {describe(event)}
          </div>
        ))}
      </div>
    </main>
  );
}
