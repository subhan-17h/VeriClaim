// Turning a locator into the source it names.
//
// Only two of the four sources are files. A spreadsheet is fetched as a grid and a
// SQL claim as the reviewed context of its table, because that is what each of those
// actually traces back to.

import { ApiError } from "./api";
import type {
  EvidenceItem,
  PolicyLocator,
  ScannedLocator,
  SheetGrid,
  TableContext
} from "../types";

const DOCUMENT_ROUTE = {
  policy: "policy",
  scanned_pdf: "scanned"
} as const;

/** The URL of the document this evidence came from, anchored at its page. */
export function documentUrl(item: EvidenceItem): string | null {
  const route = DOCUMENT_ROUTE[item.source_type as keyof typeof DOCUMENT_ROUTE];
  if (route === undefined) return null;

  const locator = item.locator as PolicyLocator | ScannedLocator;
  const url = `/api/sources/${route}/${encodeURIComponent(locator.document)}`;
  // A locator with no page opens the document at its start. Defaulting to page one
  // would claim a page the evidence never named.
  return locator.page === null ? url : `${url}#page=${locator.page}`;
}

async function read<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    let detail = `Request failed with HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) detail = body.detail;
    } catch {
      // A proxy failure answers in HTML; the status stays the useful fallback.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

/** One sheet of one workbook, as written. */
export function fetchSheet(
  workbook: string,
  sheet: string,
  signal?: AbortSignal
): Promise<SheetGrid> {
  const path = `/api/sources/spreadsheet/${encodeURIComponent(
    workbook
  )}/${encodeURIComponent(sheet)}`;
  return read<SheetGrid>(path, signal);
}

/** The reviewed context of one table. */
export function fetchTable(
  table: string,
  signal?: AbortSignal
): Promise<TableContext> {
  return read<TableContext>(
    `/api/sources/sql/${encodeURIComponent(table)}`,
    signal
  );
}
