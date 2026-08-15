import { afterEach, describe, expect, it, vi } from "vitest";

import { ask, askStream, cancelRun } from "../api";
import type { Event } from "../../types";

function ndjsonResponse(lines: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const line of lines) controller.enqueue(encoder.encode(line + "\n"));
      controller.close();
    }
  });
  return new Response(body, { status: 200 });
}

const FINAL = '{"event":"final","question":"q","answer":"a","trace_id":"t"}';

function stubFetch(response: Response) {
  const fetchStub = vi.fn(async () => response);
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("askStream", () => {
  it("returns the final event", async () => {
    stubFetch(
      ndjsonResponse([
        '{"event":"run_started","trace_id":"t","question":"q"}',
        FINAL
      ])
    );
    const seen: Event[] = [];

    const final = await askStream("q", (event) => seen.push(event));

    expect(final.event).toBe("final");
    expect(final.answer).toBe("a");
    expect(seen.map((e) => e.event)).toEqual(["run_started", "final"]);
  });

  it("never hands a ping to the caller", async () => {
    stubFetch(ndjsonResponse(['{"event":"ping"}', '{"event":"ping"}', FINAL]));
    const seen: Event[] = [];

    await askStream("q", (event) => seen.push(event));

    expect(seen.map((e) => e.event)).toEqual(["final"]);
  });

  it("throws the message carried by an error event", async () => {
    stubFetch(ndjsonResponse(['{"event":"error","message":"the run failed"}']));

    await expect(askStream("q", () => {})).rejects.toThrow("the run failed");
  });

  it("throws when the stream ends with neither final nor error", async () => {
    stubFetch(
      ndjsonResponse(['{"event":"run_started","trace_id":"t","question":"q"}'])
    );

    await expect(askStream("q", () => {})).rejects.toThrow(
      "ended without a final event"
    );
  });

  it("ignores an event name it does not know", async () => {
    stubFetch(ndjsonResponse(['{"event":"invented_later"}', FINAL]));
    const seen: Event[] = [];

    await askStream("q", (event) => seen.push(event));

    expect(seen.map((e) => e.event)).toEqual(["final"]);
  });

  it("posts the question to the streaming endpoint", async () => {
    const fetchStub = stubFetch(ndjsonResponse([FINAL]));

    await askStream("are burst pipes covered?", () => {});

    const [url, init] = fetchStub.mock.calls[0] as unknown as [
      string,
      RequestInit
    ];
    expect(url).toBe("/api/ask/stream");
    expect(JSON.parse(String(init.body))).toEqual({
      question: "are burst pipes covered?"
    });
  });
});

describe("cancellation", () => {
  it("names the run so the server can be told to stop it", async () => {
    const fetchStub = stubFetch(ndjsonResponse([FINAL]));

    await askStream("q", () => {}, { runId: "run-7" });

    const [, init] = fetchStub.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      question: "q",
      run_id: "run-7"
    });
  });

  it("cancels a run by name", async () => {
    const fetchStub = stubFetch(
      new Response(JSON.stringify({ cancelled: true }), { status: 200 })
    );

    const stopped = await cancelRun("run-7");

    const [url, init] = fetchStub.mock.calls[0] as unknown as [
      string,
      RequestInit
    ];
    expect(url).toBe("/api/runs/run-7/cancel");
    expect(init.method).toBe("POST");
    expect(stopped).toBe(true);
  });

  it("treats a run that already finished as nothing to stop", async () => {
    // The run can end between the click and the request. That is not a failure
    // the person who clicked should be told about.
    stubFetch(new Response(JSON.stringify({ detail: "gone" }), { status: 404 }));

    await expect(cancelRun("run-7")).resolves.toBe(false);
  });

  it("reports an abort as an abort, not as a truncated stream", async () => {
    // Cancelling closes the stream server-side, so the client can see a clean end
    // with no final. Calling that "the stream ended without a final event" would
    // report the user's own stop as a server fault.
    stubFetch(
      ndjsonResponse(['{"event":"run_started","trace_id":"t","question":"q"}'])
    );
    const controller = new AbortController();
    controller.abort();

    await expect(
      askStream("q", () => {}, { signal: controller.signal })
    ).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("ask", () => {
  it("returns the awaited payload", async () => {
    stubFetch(
      new Response(JSON.stringify({ question: "q", answer: "a", trace_id: "t" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    const final = await ask("q");

    expect(final.answer).toBe("a");
  });

  it("raises with the server's detail when the request fails", async () => {
    stubFetch(
      new Response(JSON.stringify({ detail: "A run needs a question to answer" }), {
        status: 422,
        headers: { "Content-Type": "application/json" }
      })
    );

    await expect(ask("")).rejects.toThrow("A run needs a question to answer");
  });
});
