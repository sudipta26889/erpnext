import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { ApprovalCard } from './components/ApprovalCard';
import { RunFeed } from './components/RunFeed';
import { Thread } from './components/Thread';
import type { ActionRequest, Thread as ThreadType } from './types';

export function App() {
  const [thread, setThread] = useState<ThreadType | null>(null);
  const [approvals, setApprovals] = useState<ActionRequest[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [t, a] = await Promise.all([api.thread(), api.approvals()]);
      setThread(t);
      setApprovals(a.action_requests ?? []);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 5000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const send = async () => {
    const message = draft.trim();
    if (!message) return;
    setDraft('');
    setSending(true);
    try {
      await api.send(message);
      await refresh();
    } finally {
      setSending(false);
    }
  };

  if (error) return <div className="ai-error">Paperclip unavailable: {error}</div>;
  if (!thread) return <div className="text-muted">Loading…</div>;

  const activeRun = thread.live_runs?.[0];
  // Honest state: no run and no comments yet means idle, not "always on".
  const idle = !activeRun && thread.comments.length === 0 && !sending;

  return (
    <div className="ai-app">
      {idle ? <div className="text-muted">Ask your CEO anything to start the conversation.</div> : null}
      <Thread comments={thread.comments ?? []} />
      {activeRun ? <RunFeed runId={activeRun.id} /> : sending ? <div className="text-muted">Waking the CEO…</div> : null}
      {approvals.map((r) => (
        <ApprovalCard key={r.id} request={r} onResolved={refresh} />
      ))}
      <div className="ai-composer">
        <textarea
          value={draft}
          rows={3}
          placeholder="Ask your CEO…"
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
