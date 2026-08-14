import { useEffect, useRef, useState } from "react";

const WORD = "VeriClaim";

// Chosen to exercise different routing paths, so a reader can watch the router work
// without inventing questions: policy only, claims database only, spreadsheet only,
// and the four-clause flagship that reaches all four sources.
const SUGGESTIONS = [
  "Are burst pipes covered under HomeSecure?",
  "How many water-damage claims were filed in March 2026?",
  "Which regions missed their Q1 inspection-compliance targets?",
  "Why did water-damage claims increase in March 2026, which regions were responsible, did those regions miss their Q1 inspection-compliance targets, and are burst pipes covered under our current HomeSecure policy?"
];

const SHORT: Record<string, string> = {
  [SUGGESTIONS[3]]: "The four-source question"
};

type EmptyStateProps = {
  onPick: (prompt: string) => void;
  active: boolean;
  disabled: boolean;
};

export function EmptyState({ onPick, active, disabled }: EmptyStateProps) {
  const [typed, setTyped] = useState("");
  const [done, setDone] = useState(false);
  const timers = useRef<number[]>([]);

  useEffect(() => {
    timers.current.forEach(window.clearTimeout);
    timers.current = [];
    setTyped("");
    setDone(false);

    if (!active) return;

    let index = 0;
    const step = () => {
      index += 1;
      setTyped(WORD.slice(0, index));
      if (index < WORD.length) {
        timers.current.push(window.setTimeout(step, 120));
      } else {
        timers.current.push(window.setTimeout(() => setDone(true), 220));
      }
    };
    timers.current.push(window.setTimeout(step, 360));

    return () => {
      timers.current.forEach(window.clearTimeout);
      timers.current = [];
    };
  }, [active]);

  return (
    <div className="hero">
      <div className="hero-desc">Property insurance claims intelligence</div>
      <div className="hero-word">
        <span>{typed}</span>
        <span className={"word-caret" + (done ? " idle" : "")} />
      </div>
      <div className="hero-chips">
        {SUGGESTIONS.map((question) => (
          <button
            key={question}
            type="button"
            className="suggest-chip"
            disabled={disabled}
            title={question}
            onClick={() => onPick(question)}
          >
            {SHORT[question] ?? question}
          </button>
        ))}
      </div>
    </div>
  );
}
