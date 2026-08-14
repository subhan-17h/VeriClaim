import { Children } from "react";
import type { ReactNode } from "react";

import type { EvidenceItem } from "../types";

export type Chunk = { text: string } | { id: string };

// Mirrors CITATION_PATTERN in src/vericlaim/citations.py. Anything that does not match
// stays prose, exactly as the resolver treats it.
const MARKER = /\[E(\d+)\]/g;

export function splitCitations(text: string): Chunk[] {
  const chunks: Chunk[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const start = match.index ?? 0;
    if (start > last) chunks.push({ text: text.slice(last, start) });
    // Number() so [E01] and [E1] are one id, as the resolver normalises them.
    chunks.push({ id: `E${Number(match[1])}` });
    last = start + match[0].length;
  }
  if (last < text.length) chunks.push({ text: text.slice(last) });
  return chunks.length > 0 ? chunks : [{ text }];
}

function reveal(id: string) {
  const card = document.getElementById(`evidence-${id}`);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.add("flash");
  window.setTimeout(() => card.classList.remove("flash"), 1400);
}

/**
 * Replace every [En] in a markdown text node with a chip that reveals its card.
 *
 * Only strings are rewritten, so a marker inside inline code or any other element is
 * left exactly as written.
 */
export function withCitations(
  children: ReactNode,
  evidence: EvidenceItem[],
  onReveal?: (id: string) => void
): ReactNode {
  const known = new Set(evidence.map((item) => item.id));

  return Children.map(children, (child) => {
    if (typeof child !== "string") return child;

    return splitCitations(child).map((chunk, index) => {
      if ("text" in chunk) return chunk.text;

      if (!known.has(chunk.id)) {
        // A marker naming evidence that does not exist must look wrong rather than
        // vanish: the answer is shown as written.
        return (
          <span className="cite missing" key={`${chunk.id}-${index}`}>
            [{chunk.id}]
          </span>
        );
      }

      return (
        <button
          type="button"
          className="cite"
          key={`${chunk.id}-${index}`}
          title={`Show ${chunk.id}`}
          onClick={() => {
            onReveal?.(chunk.id);
            reveal(chunk.id);
          }}
        >
          {chunk.id}
        </button>
      );
    });
  });
}
