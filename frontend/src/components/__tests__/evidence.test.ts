import { describe, expect, it } from "vitest";

import { rendererName } from "../EvidenceCard";

describe("the evidence dispatcher", () => {
  it("picks a renderer for every source type", () => {
    expect(rendererName("policy")).toBe("PolicyEvidence");
    expect(rendererName("sql")).toBe("SqlEvidence");
    expect(rendererName("spreadsheet")).toBe("SpreadsheetEvidence");
    expect(rendererName("scanned_pdf")).toBe("ScannedEvidence");
  });
});
