import { afterEach, describe, expect, it, vi } from "vitest";

import { documentUrl, fetchSheet, fetchTable } from "../sources";
import type { EvidenceItem } from "../../types";

function evidence(partial: Partial<EvidenceItem>): EvidenceItem {
  return {
    id: "E1",
    source_type: "policy",
    source_label: "Policy document",
    source_id: "doc",
    content: "text",
    citation: "cite",
    locator: { document: "A.pdf", page: 3, section: null, chunk_id: "c1" },
    provenance: { tool: "t", retrieved_at: "now", trace_id: null, query: null },
    confidence: 1,
    ...partial
  } as EvidenceItem;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("documentUrl", () => {
  it("anchors a policy document at the page the evidence came from", () => {
    expect(documentUrl(evidence({}))).toBe(
      "/api/sources/policy/A.pdf#page=3"
    );
  });

  it("sends a scanned document to its own route", () => {
    const item = evidence({
      source_type: "scanned_pdf",
      locator: {
        document: "CLM-1001_CLAIM_FORM.pdf",
        page: 2,
        ocr_confidence: 0.8,
        ocr_engine: "e",
        escalated: false
      }
    });

    expect(documentUrl(item)).toBe(
      "/api/sources/scanned/CLM-1001_CLAIM_FORM.pdf#page=2"
    );
  });

  it("opens a document at its start rather than inventing page one", () => {
    const item = evidence({
      locator: { document: "A.pdf", page: null, section: null, chunk_id: "c1" }
    });

    expect(documentUrl(item)).toBe("/api/sources/policy/A.pdf");
  });

  it("escapes a name so a space or a hash cannot break the URL", () => {
    const item = evidence({
      locator: { document: "A B#1.pdf", page: 1, section: null, chunk_id: "c" }
    });

    expect(documentUrl(item)).toBe("/api/sources/policy/A%20B%231.pdf#page=1");
  });

  it("has no document to open for a source that is not a file", () => {
    const item = evidence({
      source_type: "sql",
      locator: { tables: ["ops.claims"], executed_sql: "SELECT 1", row_count: 1 }
    });

    expect(documentUrl(item)).toBeNull();
  });
});

describe("fetching a source", () => {
  it("asks for the sheet the locator names", async () => {
    const stub = vi.fn(async () =>
      new Response(JSON.stringify({ sheet: "Compliance" }), { status: 200 })
    );
    vi.stubGlobal("fetch", stub);

    const grid = await fetchSheet("Loss Ratio.xlsx", "Loss Ratio");

    const [url] = stub.mock.calls[0] as unknown as [string];
    expect(url).toBe(
      "/api/sources/spreadsheet/Loss%20Ratio.xlsx/Loss%20Ratio"
    );
    expect(grid.sheet).toBe("Compliance");
  });

  it("reports a source it could not open", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "No reviewed context" }), {
          status: 404
        })
      )
    );

    await expect(fetchTable("ops.invented")).rejects.toThrow(
      "No reviewed context"
    );
  });
});
