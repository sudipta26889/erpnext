import { useEffect, useState } from "react";
import { api } from "../api";
import type { RunEvent } from "../types";

// Runs expose no SSE, so we poll the cursor-based event feed. Showing the agent's
// actual steps is what makes a 40-second reply read as work rather than a hang.
const LABELS: Record<string, string> = {
  "adapter.invoke": "Thinking",
  status: "Status",
  chunk: "",
  call_completed: "Used a tool",
  call_denied: "Tool call denied",
  call_failed: "Tool call failed",
  approval_requested: "Waiting for your approval",
  approval_resolved: "Approval resolved",
  rate_limited: "Rate limited",
  error: "Error",
};

// A run that keeps returning events without any of them carrying a seq past
// our cursor would otherwise re-fetch (and re-append) the same page forever.
// Give up polling after this many ticks in a row make no progress, rather
// than looping indefinitely.
const MAX_STALLED_TICKS = 5;

export function RunFeed({ runId }: { runId: string }) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [stalled, setStalled] = useState(false);

  useEffect(() => {
    let seq = 0;
    let cancelled = false;
    let stalledTicks = 0;

    const tick = async () => {
      if (cancelled) return;
      try {
        const { events: fresh } = await api.events(runId, seq);
        if (fresh?.length) {
          setEvents((prev) => [...prev, ...fresh]);
          const seqs = fresh
            .map((e) => e.seq)
            .filter((s): s is number => Number.isFinite(s));
          const nextSeq = seqs.length ? Math.max(seq, ...seqs) : seq;
          if (nextSeq > seq) {
            seq = nextSeq;
            stalledTicks = 0;
          } else {
            // The server sent events, but none advanced the cursor -- polling
            // again right now would just re-fetch this same page.
            stalledTicks += 1;
          }
        }
      } catch {
        // Transient failures are expected while a run starts; keep polling.
      }
      if (cancelled) return;
      if (stalledTicks >= MAX_STALLED_TICKS) {
        setStalled(true);
        return;
      }
      window.setTimeout(tick, 1000);
    };

    tick();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  // "no events yet" is not "waking": by the time this renders the run is already
  // running. Saying "waking" here is what made a run that never started look
  // identical to one that was working -- for hours.
  if (!events.length)
    return (
      <div className="ai-status">
        <span className="ai-dot" />
        CEO is working — no steps reported yet.
      </div>
    );

  return (
    <ul className="ai-run-feed">
      {events.map((e, i) => (
        <li key={`${e.seq}-${i}`}>
          <strong>{LABELS[e.eventType] ?? e.eventType}</strong>
          {e.message ? ` — ${e.message}` : ""}
        </li>
      ))}
      {stalled ? (
        <li className="ai-status">
          Feed stalled — refresh to check for updates.
        </li>
      ) : null}
    </ul>
  );
}
