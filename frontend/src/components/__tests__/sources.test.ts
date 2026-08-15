import { describe, expect, it } from "vitest";

import { citedRowIndex, sourceRendererName } from "../SourceDrawer";
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
