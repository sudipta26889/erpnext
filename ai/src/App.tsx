import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { ApprovalCard } from './components/ApprovalCard';
import { RunFeed } from './components/RunFeed';
import { Thread } from './components/Thread';
import type { ActionRequest, Thread as ThreadType } from './types';

type Boot = { enabled: boolean; company: string; agent_id: string };

export function App() {
  const [boot, setBoot] = useState<Boot | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [thread, setThread] = useState<ThreadType | null>(null);
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
      const [t, a] = await Promise.all([api.thread(), api.approvals()]);
      setThread(t);
      setApprovals(a.action_requests ?? []);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

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
      await api.send(message);
      await refresh();
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
