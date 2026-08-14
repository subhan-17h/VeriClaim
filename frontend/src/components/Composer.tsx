import { useEffect, useRef, useState } from "react";

import { Ico } from "./icons";

type ComposerProps = {
  onSend: (text: string) => void;
  busy: boolean;
};

export function Composer({ onSend, busy }: ComposerProps) {
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Grow with the content up to the height the stylesheet caps at, so a long
  // question is readable while writing it.
  useEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 140)}px`;
  }, [value]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || busy) return;
    onSend(trimmed);
    setValue("");
  };

  const ready = value.trim().length > 0 && !busy;

  return (
    <div className="composer-dock">
      <div className="composer-inner">
        <div className={"composer" + (focused ? " focused" : "")}>
          <textarea
            ref={textareaRef}
            value={value}
            placeholder={
              busy
                ? "Working..."
                : "Ask about policies, claims, spreadsheets or scanned documents..."
            }
            disabled={busy}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            rows={1}
          />
          <button
            className={"send-btn" + (ready ? " ready" : "")}
            onClick={submit}
            disabled={!ready}
            title="Send"
            type="button"
          >
            <Ico.Send />
          </button>
        </div>
        <div className="composer-hint">
          <kbd>Enter</kbd> to send <span className="sep" /> <kbd>Shift</kbd>+
          <kbd>Enter</kbd> new line
        </div>
      </div>
    </div>
  );
}
