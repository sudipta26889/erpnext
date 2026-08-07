import type { ActionRequest, RunEvent, Thread } from './types';

// Every Paperclip call is proxied through ERPNext so the board API key never
// reaches the browser and each entry point re-checks the caller's role.
async function call<T>(method: string, args: Record<string, unknown> = {}, post = false): Promise<T> {
  const url = `/api/method/${method}`;
  const csrf = (window as any).frappe?.csrf_token ?? '';
  const res = post
    ? await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': csrf },
        body: JSON.stringify(args),
      })
    : await fetch(`${url}?${new URLSearchParams(args as Record<string, string>)}`, {
        headers: { 'X-Frappe-CSRF-Token': csrf },
      });

  if (!res.ok) throw new Error(`${method} failed (${res.status})`);
  return (await res.json()).message as T;
}

export const api = {
  boot: () => call<{ enabled: boolean; company: string; agent_id: string }>('erpnext.ai.page.ai.ai.get_boot_info'),
  thread: () => call<Thread>('erpnext.ai.paperclip.get_thread'),
  send: (message: string) => call<{ issue_id: string }>('erpnext.ai.paperclip.send_message', { message }, true),
  events: (runId: string, afterSeq: number) =>
    call<{ events: RunEvent[] }>('erpnext.ai.paperclip.get_run_events', {
      run_id: runId,
      after_seq: String(afterSeq),
    }),
  approvals: () => call<{ action_requests: ActionRequest[] }>('erpnext.ai.paperclip.list_approvals'),
  resolve: (id: string, approve: boolean) =>
    // resolve_approval(approve: bool) on the server relies on Frappe coercing the
    // HTTP value to a Python bool. A raw JS boolean survives our JSON POST body
    // correctly today, but that depends on the request staying JSON-encoded — a
    // bare "false" arriving as a truthy non-empty string is a classic footgun.
    // 1/0 is unambiguous under every coercion path (JSON passthrough, cint(),
    // or a naive bool()), so send that instead of true/false.
    call<{ resolved: string }>(
      'erpnext.ai.paperclip.resolve_approval',
      { action_request_id: id, approve: approve ? 1 : 0 },
      true
    ),
};
