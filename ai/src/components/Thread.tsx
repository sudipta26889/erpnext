import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Comment } from '../types';

export function Thread({ comments }: { comments: Comment[] }) {
  return (
    <div className="ai-thread">
      {comments.map((c) => (
        <div key={c.id} className={`ai-turn ai-turn-${c.agentId ? 'agent' : 'user'}`}>
          <div className="ai-turn-author">{c.agentId ? 'CEO' : 'You'}</div>
          <Markdown remarkPlugins={[remarkGfm]}>{c.body}</Markdown>
        </div>
      ))}
    </div>
  );
}
