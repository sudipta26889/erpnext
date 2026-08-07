import { useEffect, useState } from 'react';
import { api } from '../api';
import type { RunEvent } from '../types';

// Runs expose no SSE, so we poll the cursor-based event feed. Showing the agent's
// actual steps is what makes a 40-second reply read as work rather than a hang.
const LABELS: Record<string, string> = {
  'adapter.invoke': 'Thinking',
  status: 'Status',
  chunk: '',
  call_completed: 'Used a tool',
  call_denied: 'Tool call denied',
  call_failed: 'Tool call failed',
  approval_requested: 'Waiting for your approval',
  approval_resolved: 'Approval resolved',
  rate_limited: 'Rate limited',
  error: 'Error',
};

export function RunFeed({ runId }: { runId: string }) {
  const [events, setEvents] = useState<RunEvent[]>([]);

  useEffect(() => {
    let seq = 0;
    let cancelled = false;

    const tick = async () => {
      if (cancelled) return;
      try {
        const { events: fresh } = await api.events(runId, seq);
        if (fresh?.length) {
          seq = Math.max(...fresh.map((e) => e.seq ?? seq));
          setEvents((prev) => [...prev, ...fresh]);
        }
      } catch {
        // Transient failures are expected while a run starts; keep polling.
      }
      if (!cancelled) window.setTimeout(tick, 1000);
    };

    tick();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (!events.length) return <div className="text-muted">Waking the CEO…</div>;

  return (
    <ul className="ai-run-feed">
      {events.map((e, i) => (
        <li key={`${e.seq}-${i}`}>
          <strong>{LABELS[e.eventType] ?? e.eventType}</strong>
          {e.message ? ` — ${e.message}` : ''}
        </li>
      ))}
    </ul>
  );
}
