import { describe, expect, it } from "vitest";

import { load, save, titleFromQuestion, MAX_CONVERSATIONS } from "../history";
import type { Conversation } from "../history";

function memoryStorage(seed: Record<string, string> = {}) {
  const data = new Map(Object.entries(seed));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value)
  };
}

function conversation(id: string): Conversation {
  return { id, title: `Question ${id}`, createdAt: 1, turns: [] };
}

describe("history", () => {
  it("round-trips conversations", () => {
    const storage = memoryStorage();
    save([conversation("a")], storage);
    expect(load(storage).map((c) => c.id)).toEqual(["a"]);
  });

  it("keeps at most MAX_CONVERSATIONS, newest first", () => {
    const storage = memoryStorage();
    const many = Array.from({ length: MAX_CONVERSATIONS + 5 }, (_, i) =>
      conversation(String(i))
    );
    save(many, storage);
    expect(load(storage)).toHaveLength(MAX_CONVERSATIONS);
    expect(load(storage)[0].id).toBe("0");
  });

  it("returns nothing rather than throwing on malformed stored JSON", () => {
    expect(load(memoryStorage({ "vericlaim.history.v1": "{not json" }))).toEqual([]);
  });

  it("returns nothing when the key is absent", () => {
    expect(load(memoryStorage())).toEqual([]);
  });

  it("titles a conversation from its question", () => {
    expect(titleFromQuestion("  Are burst pipes covered?  ")).toBe(
      "Are burst pipes covered?"
    );
  });

  it("truncates a long title", () => {
    const title = titleFromQuestion("x".repeat(120));
    expect(title.length).toBeLessThanOrEqual(60);
    expect(title.endsWith("...")).toBe(true);
  });
});
