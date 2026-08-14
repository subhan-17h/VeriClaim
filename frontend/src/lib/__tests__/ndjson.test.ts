import { describe, expect, it } from "vitest";

import { readNdjson, StreamUnavailableError } from "../ndjson";

function streamOf(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    }
  });
  return new Response(body);
}

describe("readNdjson", () => {
  it("emits one frame per line", async () => {
    const frames: unknown[] = [];
    await readNdjson(streamOf(['{"event":"a"}\n{"event":"b"}\n']), (f) =>
      frames.push(f)
    );
    expect(frames).toEqual([{ event: "a" }, { event: "b" }]);
  });

  it("reassembles a frame split across chunk boundaries", async () => {
    const frames: unknown[] = [];
    await readNdjson(streamOf(['{"eve', 'nt":"a","n":1}', "\n"]), (f) =>
      frames.push(f)
    );
    expect(frames).toEqual([{ event: "a", n: 1 }]);
  });

  it("emits a final line that has no trailing newline", async () => {
    const frames: unknown[] = [];
    await readNdjson(streamOf(['{"event":"a"}\n{"event":"b"}']), (f) =>
      frames.push(f)
    );
    expect(frames).toEqual([{ event: "a" }, { event: "b" }]);
  });

  it("ignores blank lines", async () => {
    const frames: unknown[] = [];
    await readNdjson(streamOf(['{"event":"a"}\n\n\n']), (f) => frames.push(f));
    expect(frames).toEqual([{ event: "a" }]);
  });

  it("rejects a response that carries no body", async () => {
    const bodyless = new Response(null, { status: 204 });
    await expect(readNdjson(bodyless, () => {})).rejects.toBeInstanceOf(
      StreamUnavailableError
    );
  });
});
