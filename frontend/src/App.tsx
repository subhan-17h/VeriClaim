import { useEffect, useRef, useState } from "react";

import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { Message, turnFromEvent } from "./components/Message";
import type { Turn } from "./components/Message";
import { Sidebar } from "./components/Sidebar";
import { askStream } from "./lib/api";
import { load, save, titleFromQuestion } from "./lib/history";
import type { Conversation } from "./lib/history";

type Theme = "dark" | "light";

const THEME_KEY = "vericlaim.theme";

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function storedTheme(): Theme {
  try {
    return window.localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export default function App() {
  const [conversations, setConversations] = useState<Conversation[]>(() => load());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch {
      // A remembered theme is a nicety; losing it must not break anything.
    }
  }, [theme]);

  useEffect(() => {
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [turns]);

  const send = async (question: string) => {
    if (busy) return;
    const conversationId = activeId ?? newId();
    setActiveId(conversationId);

    let current: Turn = {
      id: newId(),
      question,
      stages: [],
      final: null,
      error: null,
      running: true
    };
    setTurns((seen) => [...seen, current]);
    setBusy(true);

    const update = (next: Turn) => {
      current = next;
      setTurns((seen) => seen.map((turn) => (turn.id === next.id ? next : turn)));
    };

    try {
      await askStream(question, (event) => update(turnFromEvent(current, event)));
    } catch (error) {
      update({
        ...current,
        running: false,
        error: error instanceof Error ? error.message : String(error)
      });
    } finally {
      setBusy(false);
      const settled: Turn = { ...current, running: false };
      setTurns((seen) => {
        const next = seen.map((turn) => (turn.id === settled.id ? settled : turn));
        setConversations((existing) => {
          const rest = existing.filter((item) => item.id !== conversationId);
          const merged: Conversation[] = [
            {
              id: conversationId,
              title: titleFromQuestion(next[0]?.question ?? question),
              createdAt: Date.now(),
              turns: next
            },
            ...rest
          ];
          save(merged);
          return merged;
        });
        return next;
      });
    }
  };

  const openConversation = (id: string) => {
    const conversation = conversations.find((item) => item.id === id);
    if (!conversation) return;
    setActiveId(id);
    setTurns(conversation.turns);
  };

  const deleteConversation = (id: string) => {
    setConversations((current) => {
      const next = current.filter((conversation) => conversation.id !== id);
      save(next);
      return next;
    });
    if (id === activeId) {
      setActiveId(null);
      setTurns([]);
    }
  };

  const startNew = () => {
    setActiveId(null);
    setTurns([]);
  };

  const empty = turns.length === 0;

  return (
    <div
      className={
        "app" + (collapsed ? " collapsed" : "") + (empty ? " is-empty" : "")
      }
    >
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((value) => !value)}
        theme={theme}
        onTheme={setTheme}
        conversations={conversations}
        activeId={activeId}
        onSelect={openConversation}
        onDelete={deleteConversation}
        onNew={startNew}
      />
      <main className="board">
        <div className="stage">
          <EmptyState onPick={send} active={empty} disabled={busy} />
          <div className="thread-scroll" ref={scrollRef}>
            <div className="thread">
              {turns.map((turn) => (
                <Message key={turn.id} turn={turn} />
              ))}
            </div>
          </div>
        </div>
        <Composer onSend={send} busy={busy} />
      </main>
    </div>
  );
}
