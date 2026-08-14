import type { EvidenceItem, PolicyLocator } from "../../types";

export function PolicyEvidence({ item }: { item: EvidenceItem }) {
  const locator = item.locator as PolicyLocator;
  return (
    <>
      <div className="ev-meta">
        <span>{locator.document}</span>
        {locator.page !== null && <span>Page {locator.page}</span>}
        {locator.section && <span className="ev-strong">{locator.section}</span>}
      </div>
      <p className="ev-text">{item.content}</p>
    </>
  );
}
