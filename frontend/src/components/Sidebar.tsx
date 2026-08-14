import { Ico, Logo } from "./icons";
import type { Conversation } from "../lib/history";

type SidebarProps = {
  collapsed: boolean;
  onToggle: () => void;
  theme: "dark" | "light";
  onTheme: (theme: "dark" | "light") => void;
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
};

export function Sidebar({
  collapsed,
  onToggle,
  theme,
  onTheme,
  conversations,
  activeId,
  onSelect,
  onDelete,
  onNew
}: SidebarProps) {
  return (
    <aside className={"sidebar" + (collapsed ? " collapsed" : "")}>
      <div className="side-top">
        <div className="brand">
          {/* Collapsed, the mark itself is the expand control, so the rail spends no
              width on a separate button. */}
          <button
            type="button"
            className="brand-mark"
            onClick={collapsed ? onToggle : undefined}
            aria-label={collapsed ? "Expand sidebar" : "VeriClaim"}
          >
            <Logo className="brand-logo" />
            <Ico.Panel className="brand-toggle-ico" />
          </button>
          <span className="brand-word hideable">VeriClaim</span>
        </div>
        {!collapsed && (
          <button
            type="button"
            className="rail-toggle"
            onClick={onToggle}
            title="Collapse"
            aria-label="Collapse sidebar"
          >
            <Ico.Panel width={17} height={17} />
          </button>
        )}
      </div>

      <button type="button" className="new-chat" onClick={onNew} title="New question">
        <span className="plus">
          <Ico.Plus />
        </span>
        <span className="nc-label hideable">New question</span>
      </button>

      <div className="side-scroll">
        {conversations.length > 0 && (
          <div className="conversation-history hideable">
            <div className="side-label">History</div>
            <div className="conversation-list">
              {conversations.map((conversation) => (
                <div
                  key={conversation.id}
                  className={
                    "conversation-item" +
                    (conversation.id === activeId ? " active" : "")
                  }
                >
                  <button
                    type="button"
                    className="conversation-select"
                    onClick={() => onSelect(conversation.id)}
                    title={conversation.title}
                  >
                    <span>{conversation.title}</span>
                  </button>
                  <button
                    type="button"
                    className="conversation-delete"
                    onClick={() => onDelete(conversation.id)}
                    aria-label={`Delete ${conversation.title}`}
                  >
                    <Ico.Trash />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="side-foot">
        {/* Two buttons rather than one switch: the CSS marks the active theme with
            .on, and collapsed it hides the active one so the rail shows a single
            glyph that always means "switch". */}
        <div className="theme-toggle">
          <button
            type="button"
            className={theme === "dark" ? "on" : ""}
            onClick={() => onTheme("dark")}
            aria-pressed={theme === "dark"}
          >
            <Ico.Moon width={14} height={14} />
            <span className="hideable">Dark</span>
          </button>
          <button
            type="button"
            className={theme === "light" ? "on" : ""}
            onClick={() => onTheme("light")}
            aria-pressed={theme === "light"}
          >
            <Ico.Sun width={14} height={14} />
            <span className="hideable">Light</span>
          </button>
        </div>
      </div>
    </aside>
  );
}
