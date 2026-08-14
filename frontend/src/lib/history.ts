import type { Turn } from "../components/Message";

export const HISTORY_STORAGE_KEY = "vericlaim.history.v1";
export const MAX_CONVERSATIONS = 20;
const MAX_TITLE = 60;

export type Conversation = {
  id: string;
  title: string;
  createdAt: number;
  turns: Turn[];
};

export type HistoryStorage = Pick<Storage, "getItem" | "setItem">;

function defaultStorage(): HistoryStorage | null {
  try {
    return window.localStorage;
  } catch {
    // Storage can be unavailable in a private window. History is a convenience and
    // its absence must not stop a question being asked.
    return null;
  }
}

export function titleFromQuestion(question: string): string {
  const trimmed = question.trim().replace(/\s+/g, " ");
  if (trimmed.length <= MAX_TITLE) return trimmed;
  return `${trimmed.slice(0, MAX_TITLE - 3)}...`;
}

export function load(
  storage: HistoryStorage | null = defaultStorage()
): Conversation[] {
  if (!storage) return [];
  try {
    const raw = storage.getItem(HISTORY_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is Conversation =>
        typeof item === "object" && item !== null && "id" in item && "turns" in item
    );
  } catch {
    // Stored history is not worth failing a page load over.
    return [];
  }
}

export function save(
  conversations: Conversation[],
  storage: HistoryStorage | null = defaultStorage()
): void {
  if (!storage) return;
  try {
    storage.setItem(
      HISTORY_STORAGE_KEY,
      JSON.stringify(conversations.slice(0, MAX_CONVERSATIONS))
    );
  } catch {
    // A full quota must not break the interface.
  }
}
