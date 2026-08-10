import { useState } from 'react';
import { api } from '../api';
import type { ActionRequest } from '../types';

// Paperclip hands the arguments over as a JSON string. Rendered raw it is an
// unreadable wall — but it is also the only description of what is about to
// happen to the books, so it cannot just be dropped either: headline what the
// call touches, keep the full payload one click away.
function describe(summary?: string): { headline: string; body: string } {
  if (!summary) return { headline: '', body: '' };
  try {
    const parsed = JSON.parse(summary);
    const data = parsed.data ?? parsed;
    const bits = [parsed.doctype, parsed.name ?? data?.name ?? data?.title].filter(Boolean);
    return { headline: bits.join(' · '), body: JSON.stringify(parsed, null, 2) };
  } catch {
    return { headline: '', body: summary };
  }
}

const shortTool = (tool?: string) => (tool ? tool.split(':').pop() ?? tool : 'tool call');

export function ApprovalCard({ request, onResolved }: { request: ActionRequest; onResolved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { headline, body } = describe(request.summary);

  const resolve = async (approve: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await api.resolve(request.id, approve);
      onResolved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ai-approval">
      <div className="ai-approval-head">
        <span className="ai-approval-title">Approval needed</span>
        <span className="ai-approval-tool">{shortTool(request.toolName)}</span>
        {request.risk ? <span className={`ai-badge ai-badge-${request.risk}`}>{request.risk}</span> : null}
        {request.application ? <span className="ai-badge">{request.application}</span> : null}
      </div>

      {headline ? <div>{headline}</div> : null}

      {body ? (
        <details className="ai-approval-args">
          <summary>Arguments</summary>
          <pre>{body}</pre>
        </details>
      ) : null}

      {error ? <div className="ai-error">{error}</div> : null}

      <div className="ai-approval-actions">
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => resolve(true)}>
          Approve
        </button>
        <button className="btn btn-default btn-sm" disabled={busy} onClick={() => resolve(false)}>
          Decline
        </button>
      </div>
    </div>
  );
}
