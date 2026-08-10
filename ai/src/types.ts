export type Comment = {
  id: string;
  body: string;
  authorType?: string;
  // Board chat writes both sides of the exchange as authorType 'user'; the
  // concierge's own turns are marked only by this sentinel author id.
  authorUserId?: string | null;
  agentId?: string | null;
  createdAt: string;
};

export const CONCIERGE = 'board-concierge';

export type BoardThread = {
  issue_id: string | null;
  comments: Comment[];
};

export type RunEvent = {
  seq: number;
  eventType: string;
  message?: string | null;
  payload?: Record<string, unknown>;
};

export type ActionRequest = {
  id: string;
  toolName?: string;
  summary?: string;
  createdAt?: string;
};

export type Thread = {
  issue_id: string;
  comments: Comment[];
  live_runs: Array<{ id: string; status: string }>;
};
