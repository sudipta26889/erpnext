import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CONCIERGE, type Comment } from '../types';

// Board chat stores its replies as authorType 'user' with a sentinel author id,
// so agentId alone would label the concierge's own turns as "You".
const author = (c: Comment) =>
  c.authorUserId === CONCIERGE ? 'Board' : c.agentId ? 'CEO' : 'You';

export function Thread({ comments }: { comments: Comment[] }) {
  return (
    <div className="ai-thread">
      {comments.map((c) => (
        <div key={c.id} className={`ai-turn ai-turn-${author(c) === 'You' ? 'user' : 'agent'}`}>
          <div className="ai-turn-author">{author(c)}</div>
          <Markdown remarkPlugins={[remarkGfm]}>{c.body}</Markdown>
        </div>
      ))}
    </div>
  );
}
