import { useState } from 'react';
import { api } from '../api';
import type { ActionRequest } from '../types';

export function ApprovalCard({ request, onResolved }: { request: ActionRequest; onResolved: () => void }) {
  const [busy, setBusy] = useState(false);

  const resolve = async (approve: boolean) => {
    setBusy(true);
    try {
      await api.resolve(request.id, approve);
      onResolved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ai-approval">
      <div className="ai-approval-title">
        Approval needed: <code>{request.toolName ?? 'tool call'}</code>
      </div>
      {request.summary ? <p>{request.summary}</p> : null}
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
