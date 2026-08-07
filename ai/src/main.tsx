import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';

// Exposed as a global because the bundle is mounted into a Frappe desk page
// rather than owning its own document.
export function mountAI(element: HTMLElement) {
  createRoot(element).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
}

(window as unknown as { mountAI: typeof mountAI }).mountAI = mountAI;
