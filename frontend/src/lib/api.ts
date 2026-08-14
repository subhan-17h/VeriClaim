import { readNdjson, StreamUnavailableError } from "./ndjson";
import { EVENT_NAMES } from "../types";
import type { Event, FinalEvent } from "../types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const KNOWN_EVENTS = new Set<string>(EVENT_NAMES);

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    // Proxy failures answer in HTML, so the status stays the useful fallback.
  }
  return `Request failed with HTTP ${response.status}`;
}

function post(
  path: string,
  question: string,
  signal?: AbortSignal
): Promise<Response> {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal
  });
}

/** Run one question, reporting each event as it arrives. */
export async function askStream(
  question: string,
  onEvent: (event: Event) => void,
  signal?: AbortSignal
): Promise<FinalEvent> {
  const response = await post("/api/ask/stream", question, signal);
  if (!response.ok) {
    throw new StreamUnavailableError(await errorDetail(response));
  }

  // A mutable holder rather than two closed-over `let`s: assigning to a captured
  // binding from inside a callback defeats TypeScript's narrowing, and this keeps the
  // types honest without a cast.
  const outcome: { final: FinalEvent | null; failure: string | null } = {
    final: null,
    failure: null
  };

  await readNdjson(
    response,
    (frame) => {
      const name = (frame as { event?: unknown }).event;
      if (typeof name !== "string") return;
      // A keepalive is a property of the connection, not of the run. Dropping it
      // here is what keeps `Event` free of it.
      if (name === "ping") return;
      // An event a newer server added must not break an older client, or every
      // protocol addition becomes a breaking change.
      if (!KNOWN_EVENTS.has(name)) return;

      const event = frame as Event;
      onEvent(event);
      if (event.event === "final") outcome.final = event;
      if (event.event === "error") outcome.failure = event.message;
    },
    signal
  );

  if (outcome.failure !== null) throw new Error(outcome.failure);
  if (outcome.final === null) {
    // The awaited route enforces this server-side; the streaming route does not.
    throw new Error("The stream ended without a final event.");
  }
  return outcome.final;
}

/** Run one question and wait for the whole answer. */
export async function ask(
  question: string,
  signal?: AbortSignal
): Promise<FinalEvent> {
  const response = await post("/api/ask", question, signal);
  if (!response.ok) {
    throw new ApiError(response.status, await errorDetail(response));
  }
  return (await response.json()) as FinalEvent;
}
