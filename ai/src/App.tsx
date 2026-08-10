import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { ApprovalCard } from './components/ApprovalCard';
import { RunFeed } from './components/RunFeed';
import { Thread } from './components/Thread';
import type { ActionRequest, BoardThread, Thread as ThreadType } from './types';

type Boot = { enabled: boolean; company: string; agent_id: string };

// Two channels, one tab. The board concierge answers in one turn, in-request --
// that is the conversational one, so it is the default. The CEO agent instead
// wakes on the comment and works with the ERPNext tools, which takes a run.
type Mode = 'board' | 'ceo';

export function App() {
  const [boot, setBoot] = useState<Boot | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>('board');
  const [thread, setThread] = useState<ThreadType | null>(null);
  const [board, setBoard] = useState<BoardThread | null>(null);
  const [approvals, setApprovals] = useState<ActionRequest[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

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
        mode === 'board' ? api.boardThread() : api.thread(),
        api.approvals(),
      ]);
      if (mode === 'board') setBoard(t as BoardThread);
      else setThread(t as ThreadType);
      setApprovals(a.action_requests ?? []);
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

  const send = async () => {
    const message = draft.trim();
    if (!message) return;
    setDraft('');
    setSending(true);
    try {
      // The board reply is persisted as a comment either way, so even if this
      // request dies (proxy timeout, closed tab) the next poll still shows it.
      if (mode === 'board') await api.boardChat(message);
      else await api.send(message);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  if (bootError) return <div className="ai-error">Could not load AI status: {bootError}</div>;
  if (!boot) return <div className="text-muted">Loading…</div>;
  if (!configured) {
    return (
      <div className="ai-error">
        ERPNext AI is not configured yet. Enable it and bind a Company and Agent ID in AI Settings.
      </div>
    );
  }

  if (error) return <div className="ai-error">Paperclip unavailable: {error}</div>;

  const comments = (mode === 'board' ? board?.comments : thread?.comments) ?? null;
  if (!comments) return <div className="text-muted">Loading…</div>;

  const activeRun = mode === 'ceo' ? thread?.live_runs?.[0] : undefined;
  // Honest state: no run and no comments yet means idle, not "always on".
  const idle = !activeRun && comments.length === 0 && !sending;

  return (
    <div className="ai-app">
      <div className="ai-modes">
        <button
          className={`btn btn-sm ${mode === 'board' ? 'btn-primary' : 'btn-default'}`}
          onClick={() => setMode('board')}
        >
          Board room
        </button>
        <button
          className={`btn btn-sm ${mode === 'ceo' ? 'btn-primary' : 'btn-default'}`}
          onClick={() => setMode('ceo')}
        >
          CEO
        </button>
      </div>
      {idle ? (
        <div className="text-muted">
          {mode === 'board'
            ? 'Ask the board room about the company, its issues and its agents.'
            : 'Ask your CEO to act — it works the ERPNext tools and comes back for approvals.'}
        </div>
      ) : null}
      <Thread comments={comments} />
      {activeRun ? (
        <RunFeed runId={activeRun.id} />
      ) : sending ? (
        <div className="text-muted">{mode === 'board' ? 'Board room is thinking…' : 'Waking the CEO…'}</div>
      ) : null}
      {approvals.map((r) => (
        <ApprovalCard key={r.id} request={r} onResolved={refresh} />
      ))}
      <div className="ai-composer">
        <textarea
          value={draft}
          rows={3}
          placeholder={mode === 'board' ? 'Ask the board room…' : 'Ask your CEO…'}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send();
          }}
        />
        <button className="btn btn-primary" disabled={sending} onClick={send}>
          Send
        </button>
      </div>
    </div>
  );
}
