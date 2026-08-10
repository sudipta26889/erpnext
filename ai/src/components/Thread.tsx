import { useEffect, useRef } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CONCIERGE, type Comment } from '../types';

// Board chat stores its replies as authorType 'user' with a sentinel author id,
// so agentId alone would label the concierge's own turns as "You".
const author = (c: Comment) => (c.authorUserId === CONCIERGE ? 'Board' : c.agentId ? 'CEO' : 'You');

const time = (iso?: string) => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
};

export function Thread({ comments }: { comments: Comment[] }) {
  const end = useRef<HTMLDivElement>(null);

  // Follow the conversation. A transcript this long otherwise opens at the very
  // first message, which reads as "nothing happened here in months".
  useEffect(() => {
    // Optional-call: jsdom (and any non-browser host) has no scrollIntoView, and a
    // throw here unmounts the whole tab.
    end.current?.scrollIntoView?.({ block: "end" });
  }, [comments.length]);

  return (
    <div className="ai-thread">
      {comments.map((c) => {
        const who = author(c);
        return (
          <div key={c.id} className={`ai-turn ai-turn-${who === 'You' ? 'user' : 'agent'}`}>
            <div className="ai-turn-meta">
              <span className="ai-turn-author">{who}</span>
              <span>{time(c.createdAt)}</span>
            </div>
            <div className="ai-bubble">
              <Markdown remarkPlugins={[remarkGfm]}>{c.body}</Markdown>
            </div>
          </div>
        );
      })}
      <div ref={end} />
    </div>
  );
}
