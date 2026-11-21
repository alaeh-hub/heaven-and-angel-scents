import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import { MotionConfig } from 'motion/react';
import App from './App.jsx';
import './styles/tailwind.css';
import './styles/tokens.css';
import './styles/landing.css';
import './styles/detail.css';
import './styles/catalog.css';
import './styles/skin.css';
import './styles/atelier.css';
import './styles/square.css';
import './styles/reveal.css';

// Start at the top on reload (restored scroll lands mid-reflow).
// The `js` class lets hover-revealed details hide only when scripts run.
document.documentElement.classList.add('js', 'dark');

if ('scrollRestoration' in window.history) {
  window.history.scrollRestoration = 'manual';
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {/* Reduced-motion visitors keep fades but lose movement. */}
    <MotionConfig reducedMotion="user">
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </MotionConfig>
  </StrictMode>,
);
