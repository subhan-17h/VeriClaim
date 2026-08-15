// The wire contract, mirroring src/vericlaim/api/protocol.py.
//
// `ping` is deliberately absent. A keepalive is a property of the connection, not of
// the run; lib/api.ts drops it before a frame reaches any of these types.

export type SourceType = "policy" | "sql" | "spreadsheet" | "scanned_pdf";

export type PolicyLocator = {
  document: string;
  page: number | null;
  section: string | null;
  chunk_id: string;
};

export type ScannedLocator = {
  document: string;
  page: number | null;
  ocr_confidence: number | null;
  ocr_engine: string;
  escalated: boolean;
};

export type SpreadsheetLocator = {
  workbook: string;
  sheet: string;
  row: number | null;
  a1_range: string;
};

export type SqlLocator = {
  tables: string[];
  executed_sql: string;
  row_count: number;
};

// A union rather than an optional-everything object, so a renderer switching on
// source_type cannot read a field the locator does not have. C-10.4 builds one
// renderer per source type against exactly this.
export type Locator =
  | PolicyLocator
  | ScannedLocator
  | SpreadsheetLocator
  | SqlLocator;

export type Provenance = {
  tool: string;
  retrieved_at: string;
  trace_id: string | null;
  query: string | null;
};

export type EvidenceItem = {
  id: string;
  source_type: SourceType;
  source_label: string;
  source_id: string;
  content: string;
  citation: string;
  locator: Locator;
  provenance: Provenance;
  confidence: number;
};

// cost_usd is this stage's own model call and is accurate. It must never be summed
// into a run total: a source tool's model calls reach no stage. See C-9.6.
export type StageRecord = {
  name: string;
  detail: Record<string, unknown>;
  model: string;
  cost_usd: number;
  latency_ms: number;
  error: string;
};

export type CitationReport = {
  ok: boolean;
  resolved: string[];
  unresolved: string[];
  malformed: string[];
  uncited: string[];
  precision: number;
  coverage: number;
  verified: boolean;
  regenerated: boolean;
  degraded: boolean;
  problems: string[];
};

export type RoutingDecision = {
  sources: SourceType[];
  confidence: number;
  reason: string;
  out_of_scope: boolean;
  needs_clarification: boolean;
  clarification_question: string;
};

export type RunStartedEvent = {
  event: "run_started";
  trace_id: string;
  question: string;
};

export type StageEvent = {
  event: "stage";
  name: string;
  model: string;
  cost_usd: number;
  latency_ms: number;
  error: string;
  detail: Record<string, unknown>;
};

// items is an array though the server emits exactly one per event, so batching per
// source stays a non-breaking change. See C-9.6.
export type EvidenceEvent = {
  event: "evidence";
  source: SourceType;
  items: EvidenceItem[];
};

export type FinalEvent = {
  event: "final";
  question: string;
  answer: string;
  trace_id: string;
  evidence: EvidenceItem[];
  sources_used: SourceType[];
  routing: RoutingDecision | null;
  citations: CitationReport;
  stages: StageRecord[];
  failures: string[];
  replans: number;
  latency_ms: number;
  // The gateway ledger's figure, not a sum of stages.
  cost_usd: number;
  understanding: Record<string, unknown>;
  plans: Record<string, unknown>;
  collection: Record<string, unknown>;
  sufficiency: Record<string, unknown>;
};

export type ErrorEvent = {
  event: "error";
  message: string;
};

export type Event =
  | RunStartedEvent
  | StageEvent
  | EvidenceEvent
  | FinalEvent
  | ErrorEvent;

export const EVENT_NAMES = [
  "run_started",
  "stage",
  "evidence",
  "final",
  "error"
] as const;

// What GET /api/sources/spreadsheet/{workbook}/{sheet} returns. The sheet as written:
// rows[i] is spreadsheet row first_row + i, banner and blank rows included.
export type SheetGrid = {
  workbook: string;
  sheet: string;
  columns: string[];
  first_row: number;
  rows: string[][];
  total_rows: number;
  truncated: boolean;
};

export type TableColumn = {
  name: string;
  type: string;
  meaning: string;
  unit?: string;
};

// What GET /api/sources/sql/{table} returns: context_detail's planning view.
export type TableContext = {
  table: string;
  purpose: string;
  columns: TableColumn[];
  useful_for: string[];
  joins: { column: string; references: string; meaning: string }[];
  cautions: string[];
};
