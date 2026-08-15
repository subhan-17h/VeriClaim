import { describe, expect, it } from "vitest";

import { citedRow, citedRowIndex, sourceRendererName } from "../SourceDrawer";
import type { SheetGrid } from "../../types";

const GRID: SheetGrid = {
  workbook: "Book.xlsx",
  sheet: "Compliance",
  columns: ["A", "B"],
  first_row: 1,
  rows: [["banner", ""], ["", ""], ["region", "done"], ["North", "98"]],
  total_rows: 4,
  truncated: false
};

describe("the source dispatcher", () => {
  it("opens every source type", () => {
    expect(sourceRendererName("policy")).toBe("PdfSource");
    expect(sourceRendererName("scanned_pdf")).toBe("PdfSource");
    expect(sourceRendererName("spreadsheet")).toBe("SheetSource");
    expect(sourceRendererName("sql")).toBe("TableSource");
  });
});

describe("which row a spreadsheet locator cites", () => {
  it("reads the row from the A1 range when the locator names no row", () => {
    // Every spreadsheet citation this corpus produces carries a range and no row
    // number, so highlighting only on `row` would never highlight anything.
    expect(citedRow({ workbook: "w", sheet: "s", row: null, a1_range: "A4:E4" })).toBe(4);
  });

  it("prefers the row the locator states outright", () => {
    expect(citedRow({ workbook: "w", sheet: "s", row: 9, a1_range: "A4:E4" })).toBe(9);
  });

  it("has no row when the locator carries neither", () => {
    expect(citedRow({ workbook: "w", sheet: "s", row: null, a1_range: "" })).toBeNull();
  });

  it("has no row when the range names no row, as an aggregate's does not", () => {
    expect(citedRow({ workbook: "w", sheet: "s", row: null, a1_range: "A:E" })).toBeNull();
  });
});

describe("finding the cited row", () => {
  it("maps a spreadsheet row number onto the grid", () => {
    expect(citedRowIndex(GRID, 4)).toBe(3);
  });

  it("has nothing to highlight when the locator names no row", () => {
    expect(citedRowIndex(GRID, null)).toBeNull();
  });

  it("has nothing to highlight when the row is outside a truncated grid", () => {
    expect(citedRowIndex(GRID, 99)).toBeNull();
  });
});
