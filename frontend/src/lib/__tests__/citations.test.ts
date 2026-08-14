import { describe, expect, it } from "vitest";

import { splitCitations } from "../citations";

describe("splitCitations", () => {
  it("splits prose from markers", () => {
    expect(splitCitations("Covered [E1] and paid [E2].")).toEqual([
      { text: "Covered " },
      { id: "E1" },
      { text: " and paid " },
      { id: "E2" },
      { text: "." }
    ]);
  });

  it("returns a single chunk when there are no markers", () => {
    expect(splitCitations("No citations here.")).toEqual([
      { text: "No citations here." }
    ]);
  });

  it("normalises a leading zero so [E01] and [E1] are one id", () => {
    expect(splitCitations("[E01]")).toEqual([{ id: "E1" }]);
  });

  it("leaves a malformed marker as prose", () => {
    expect(splitCitations("As shown [E] and [EX].")).toEqual([
      { text: "As shown [E] and [EX]." }
    ]);
  });

  it("handles two markers with nothing between them", () => {
    expect(splitCitations("[E1][E2]")).toEqual([{ id: "E1" }, { id: "E2" }]);
  });
});
