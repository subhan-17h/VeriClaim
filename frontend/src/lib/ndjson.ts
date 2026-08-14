// A line reader over an NDJSON response body.
//
// Adapted from the CSRS frontend. Chunk boundaries fall wherever the network puts
// them, including mid-JSON, so lines are buffered and only complete ones parsed. The
// buffer is flushed after the stream closes, because a server is not obliged to end
// its last line with a newline.

export class StreamUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StreamUnavailableError";
  }
}

export class StreamReadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StreamReadError";
  }
}

export async function readNdjson(
  response: Response,
  onFrame: (frame: unknown) => void,
  signal?: AbortSignal
): Promise<void> {
  if (!response.body) {
    throw new StreamUnavailableError(
      "The streaming response did not include a body."
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const processLine = (line: string) => {
    if (!line.trim()) return;
    onFrame(JSON.parse(line));
  };

  try {
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (error) {
        // An aborted read is the caller's own doing and must stay distinguishable
        // from a transport failure, which is a TypeError from fetch.
        if (error instanceof Error && error.name === "AbortError") throw error;
        if (signal?.aborted) {
          throw new DOMException("The operation was aborted.", "AbortError");
        }
        if (error instanceof TypeError) throw new StreamReadError(error.message);
        throw error;
      }

      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      lines.forEach(processLine);
    }

    buffer += decoder.decode();
    processLine(buffer);
  } finally {
    reader.releaseLock();
  }
}
