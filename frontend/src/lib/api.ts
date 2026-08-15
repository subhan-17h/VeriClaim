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
  body: Record<string, unknown>,
  signal?: AbortSignal
): Promise<Response> {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal
  });
}

function aborted(): DOMException {
  return new DOMException("The operation was aborted.", "AbortError");
}

/**
 * Stop a run the server is still working on.
 *
 * A disconnect cannot do this: the server drives the stream through a threadpool
 * iterator it never closes, so hanging up frees the run only whenever the garbage
 * collector reaches it. Naming the run is what makes the stop immediate.
 *
 * Resolves false when there was no such run -- it can finish between the click and
 * this request, which is not a failure worth showing anyone.
 */
export async function cancelRun(runId: string): Promise<boolean> {
  const response = await post(`/api/runs/${encodeURIComponent(runId)}/cancel`, {});
  if (response.status === 404) return false;
  if (!response.ok) throw new ApiError(response.status, await errorDetail(response));
  return true;
}

export type AskOptions = {
  signal?: AbortSignal;
  /** Minted by the caller so this run can be cancelled by name. */
  runId?: string;
};

/** Run one question, reporting each event as it arrives. */
export async function askStream(
  question: string,
  onEvent: (event: Event) => void,
  options: AskOptions = {}
): Promise<FinalEvent> {
  const { signal, runId } = options;
  const response = await post(
    "/api/ask/stream",
    runId === undefined ? { question } : { question, run_id: runId },
    signal
  );
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

  // Cancelling ends the stream server-side, so a stopped run can arrive here as a
  // clean end with no final. Reporting that as a truncated stream would blame the
  // server for what the person watching just asked for.
  if (signal?.aborted) throw aborted();
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
  const response = await post("/api/ask", { question }, signal);
  if (!response.ok) {
    throw new ApiError(response.status, await errorDetail(response));
  }
  return (await response.json()) as FinalEvent;
}
