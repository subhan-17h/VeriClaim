import { documentUrl } from "../../lib/sources";
import type { EvidenceItem, PolicyLocator, ScannedLocator } from "../../types";

/** A document, rendered by the browser's own viewer at the page the evidence cites. */
export function PdfSource({ item }: { item: EvidenceItem }) {
  const url = documentUrl(item);
  const locator = item.locator as PolicyLocator | ScannedLocator;
  if (url === null) return null;

  return (
    <>
      <div className="source-meta">
        {locator.document}
        {locator.page === null ? "" : ` · p.${locator.page}`}
      </div>
      <iframe className="source-frame" src={url} title={locator.document} />
    </>
  );
}
