import { useEffect, useRef, useState } from "react";

import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { Message, turnFromEvent } from "./components/Message";
import type { Turn } from "./components/Message";
import { Sidebar } from "./components/Sidebar";
import { askStream, cancelRun } from "./lib/api";
import { load, save, titleFromQuestion } from "./lib/history";
import type { Conversation } from "./lib/history";

type Theme = "dark" | "light";

const THEME_KEY = "vericlaim.theme";

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
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
  // What the stop button acts on: the run's name on the server, and this page's
  // side of the connection.
  const live = useRef<{ runId: string; abort: AbortController } | null>(null);

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
      running: true,
      stopped: false
    };
    setTurns((seen) => [...seen, current]);
    setBusy(true);

    const update = (next: Turn) => {
      current = next;
      setTurns((seen) => seen.map((turn) => (turn.id === next.id ? next : turn)));
    };

    const runId = newId();
    const abort = new AbortController();
    live.current = { runId, abort };

    try {
      await askStream(question, (event) => update(turnFromEvent(current, event)), {
        signal: abort.signal,
        runId
      });
    } catch (error) {
      // A stop is not a failure, and must not be reported as one.
      update(
        isAbort(error)
          ? { ...current, running: false, stopped: true }
          : {
              ...current,
              running: false,
              error: error instanceof Error ? error.message : String(error)
            }
      );
    } finally {
      live.current = null;
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

  const stop = () => {
    const running = live.current;
    if (!running) return;
    // Tell the server first: aborting only closes this page's connection, which the
    // server may not notice for a long time, and the run would keep spending.
    void cancelRun(running.runId).catch(() => {
      // Already finished, or the request itself failed. Either way the abort below
      // is what this page needs, and there is nothing here to tell anyone.
    });
    running.abort.abort();
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
        <Composer onSend={send} onStop={stop} busy={busy} />
      </main>
    </div>
  );
}
