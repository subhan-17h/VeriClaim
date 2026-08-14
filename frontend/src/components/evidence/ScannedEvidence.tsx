import type { EvidenceItem, ScannedLocator } from "../../types";

export function ScannedEvidence({ item }: { item: EvidenceItem }) {
  const locator = item.locator as ScannedLocator;
  const confidence = locator.ocr_confidence;

  return (
    <>
      <div className="ev-meta">
        <span>{locator.document}</span>
        {locator.page !== null && <span>Page {locator.page}</span>}
        {locator.escalated && (
          <span className="ev-strong">re-read by the vision tier</span>
        )}
      </div>
      {/* The measured confidence, not a verdict on it. The floor that decides what
          counts as low is a Python setting and is not on the wire; a second copy of
          it here would drift the first time it is tuned. The qualification a low
          reading needs is already carried by the answer text. */}
      {confidence !== null && (
        <div className="ev-score">
          <span>OCR confidence</span>
          <div className="hbar-track">
            <div
              className="hbar-fill"
              style={{ width: `${Math.round(confidence * 100)}%` }}
            />
          </div>
          <span className="ev-score-value">{confidence.toFixed(2)}</span>
        </div>
      )}
      <p className="ev-text">{item.content}</p>
    </>
  );
}
