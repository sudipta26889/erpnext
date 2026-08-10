import { Component, StrictMode, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './styles.css';

// A throw inside render unmounts the entire root: the tab goes blank with the
// message only in the console, which is indistinguishable from "the bundle never
// loaded". One malformed field from Paperclip did exactly that. Show it instead.
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error('erpnext-ai render failed', error);
  }

  render() {
    if (this.state.error) {
      return <div className="ai-error">AI failed to render: {this.state.error.message}</div>;
    }
    return this.props.children;
  }
}

// Exposed as a global because the bundle is mounted into a Frappe desk page
// rather than owning its own document.
export function mountAI(element: HTMLElement) {
  createRoot(element).render(
    <StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </StrictMode>
  );
}

(window as unknown as { mountAI: typeof mountAI }).mountAI = mountAI;
