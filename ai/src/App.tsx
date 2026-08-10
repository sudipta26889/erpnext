import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ApprovalCard } from "./components/ApprovalCard";
import { RunFeed } from "./components/RunFeed";
import { Thread } from "./components/Thread";
import type { ActionRequest, BoardThread, Thread as ThreadType } from "./types";

type Boot = { enabled: boolean; company: string; agent_id: string };

// Two channels, one tab. The board concierge answers in one turn, in-request --
// that is the conversational one, so it is the default. The CEO agent instead
// wakes on the comment and works with the ERPNext tools, which takes a run.
type Mode = "board" | "ceo";

const HINT: Record<Mode, string> = {
  board: "Answers now. Knows the company, its issues and its agents.",
  ceo: "Works the ERPNext tools and comes back for approvals. Replies as a run.",
};

export function App() {
  const [boot, setBoot] = useState<Boot | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("board");
  const [thread, setThread] = useState<ThreadType | null>(null);
  const [board, setBoard] = useState<BoardThread | null>(null);
  const [approvals, setApprovals] = useState<ActionRequest[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const dock = useRef<HTMLDivElement>(null);
  const app = useRef<HTMLDivElement>(null);
  const [dockHeight, setDockHeight] = useState(0);

  // The dock is sticky, so the thread scrolls *under* it: without reserving its
  // height the newest turn ends up hidden behind the approvals -- the same
  // "you cannot see the thing that matters" failure the dock exists to fix.
  // Its height changes with the number of approvals, so measure rather than guess.
  useEffect(() => {
    const el = dock.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      app.current?.style.setProperty(
        "--ai-dock-height",
        `${el.offsetHeight}px`,
      );
    });
    observer.observe(el);
    return () => observer.disconnect();
  });

  // get_boot_info() tells us whether AI is enabled/configured *before* we
  // ever call get_thread()/list_approvals() -- those 500 against an
  // unconfigured Paperclip client, which used to be the only signal a
  // disabled site gave: "Paperclip unavailable: ... failed (500)" instead
  // of an actionable configuration prompt.
  useEffect(() => {
    let cancelled = false;
    api
      .boot()
      .then((b) => {
        if (!cancelled) setBoot(b);
      })
      .catch((e) => {
        if (!cancelled) setBootError((e as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const configured = Boolean(boot?.enabled && boot?.company && boot?.agent_id);

  const refresh = useCallback(async () => {
    try {
      const [t, a] = await Promise.all([
        mode === "board" ? api.boardThread() : api.thread(),
        api.approvals(),
      ]);
      if (mode === "board") setBoard(t as BoardThread);
      else setThread(t as ThreadType);
      // A non-array here used to throw inside render and unmount the whole tab,
      // which looks exactly like 'the page is broken' and says nothing.
      setApprovals(Array.isArray(a.action_requests) ? a.action_requests : []);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [mode]);

  useEffect(() => {
    if (!configured) return;
    refresh();
    const id = window.setInterval(refresh, 5000);
    return () => window.clearInterval(id);
  }, [refresh, configured]);

  const count =
    (mode === "board" ? board?.comments : thread?.comments)?.length ?? 0;
  const anchor = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // scrollIntoView, not window.scrollTo: the desk scrolls an inner container,
    // so scrolling the window is a silent no-op and the thread opens at its
    // oldest message -- which, on a months-long transcript, reads as "nothing
    // happened here". The anchor carries scroll-margin-bottom equal to the dock
    // height, so the newest turn lands above the approvals rather than under them.
    anchor.current?.scrollIntoView?.({ block: "end" });
  }, [count, dockHeight, mode]);

  const send = async () => {
    const message = draft.trim();
    if (!message) return;
    setDraft("");
    setSending(true);
    try {
      // The board reply is persisted as a comment either way, so even if this
      // request dies (proxy timeout, closed tab) the next poll still shows it.
      if (mode === "board") await api.boardChat(message);
      else await api.send(message);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  if (bootError)
    return (
      <div className="ai-error">Could not load AI status: {bootError}</div>
    );
  if (!boot) return <div className="ai-status">Loading…</div>;
  if (!configured) {
    return (
      <div className="ai-error">
        ERPNext AI is not configured yet. Enable it and bind a Company and Agent
        ID in AI Settings.
      </div>
    );
  }

  const raw = mode === "board" ? board?.comments : thread?.comments;
  const comments = raw === undefined ? null : Array.isArray(raw) ? raw : [];
  const activeRun = mode === "ceo" ? thread?.live_runs?.[0] : undefined;

  return (
    <div className="ai-app" ref={app}>
      <div className="ai-modes">
        <div className="ai-segmented" role="group" aria-label="Who answers">
          <button
            aria-pressed={mode === "board"}
            onClick={() => setMode("board")}
          >
            Board room
          </button>
          <button aria-pressed={mode === "ceo"} onClick={() => setMode("ceo")}>
            CEO
          </button>
        </div>
        <span className="ai-mode-hint">{HINT[mode]}</span>
      </div>

      {/* An error replaces the feed but never the composer: losing the input box
          mid-conversation because one poll failed is its own bug. */}
      {error ? (
        <div className="ai-error">Paperclip unavailable: {error}</div>
      ) : null}

      {comments === null ? (
        <div className="ai-status">Loading…</div>
      ) : comments.length === 0 && !sending ? (
        <div className="ai-empty">
          {mode === "board"
            ? "Ask the board room about the company, its issues and its agents."
            : "Ask your CEO to act — it works the ERPNext tools and comes back for approvals."}
        </div>
      ) : (
        <Thread comments={comments} />
      )}

      {activeRun ? (
        <RunFeed runId={activeRun.id} />
      ) : sending ? (
        <div className="ai-status">
          <span className="ai-dot" />
          {mode === "board" ? "Board room is thinking…" : "Waking the CEO…"}
        </div>
      ) : null}

      <div className="ai-scroll-anchor" ref={anchor} />

      <div className="ai-dock" ref={dock}>
        {approvals.length ? (
          <div className="ai-approvals">
            <div className="ai-approvals-head">
              {approvals.length} approval{approvals.length === 1 ? "" : "s"}{" "}
              waiting on you
            </div>
            {approvals.map((r) => (
              <ApprovalCard key={r.id} request={r} onResolved={refresh} />
            ))}
          </div>
        ) : null}

        <div className="ai-composer">
          <textarea
            value={draft}
            rows={2}
            placeholder={
              mode === "board" ? "Ask the board room…" : "Ask your CEO…"
            }
            disabled={sending}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter is a newline -- chat convention.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <div className="ai-composer-side">
            <button
              className="btn btn-primary"
              disabled={sending || !draft.trim()}
              onClick={send}
            >
              Send
            </button>
            <span className="ai-composer-hint">Enter to send</span>
          </div>
        </div>
      </div>
    </div>
  );
}
