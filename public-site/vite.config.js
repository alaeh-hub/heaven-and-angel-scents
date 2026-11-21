import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath } from 'node:url';

// Dev: proxy the API and /static to Flask on :5000 (same-origin, no CORS).
// Build: writes to ../static/public-site/, served by Flask.
const FLASK = process.env.FLASK_URL || 'http://127.0.0.1:5000';

export default defineConfig(({ command }) => ({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // Only the build lives under /static; dev serves from /.
  base: command === 'build' ? '/static/public-site/' : '/',
  build: {
    outDir: '../static/public-site',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    host: true,
    // VS Code / dev-tunnel hostnames.
    allowedHosts: true,
    proxy: {
      '^/partner-portal/[^/]+/api/.*': FLASK,
      '/static': FLASK,
    },
  },
}));
