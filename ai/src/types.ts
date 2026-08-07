export type Comment = {
  id: string;
  body: string;
  authorType?: string;
  agentId?: string | null;
  createdAt: string;
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
