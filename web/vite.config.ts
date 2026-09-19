import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Issue #40: proxies every backend route prefix to the local FastAPI
    // process, so the frontend's own base URLs (see src/api/baseUrl.ts) can
    // default to '' (same-origin, relative requests) instead of a hardcoded
    // absolute localhost:8000 - this is what makes plain `npm run dev`
    // behave the same way a single tunneled origin does, with zero CORS
    // involved either way (the browser never sees a cross-origin request).
    proxy: {
      '/characters': 'http://localhost:8000',
      '/companions': 'http://localhost:8000',
      '/campaigns': 'http://localhost:8000',
      '/sessions': 'http://localhost:8000',
      '/media': 'http://localhost:8000',
      '/ws': { target: 'http://localhost:8000', ws: true, changeOrigin: true },
    },
    // A free Cloudflare Tunnel quick-tunnel gets a fresh random hostname
    // every time it starts, so there's no fixed value to allowlist here -
    // `true` disables Vite's own dev-server host check entirely, trusting
    // network-level access control instead (see issues #41/#42's tracking
    // of the debug_action backdoor and an invite-passphrase gate - this
    // alone isn't real security, just what's needed for a tunnel hostname
    // to load at all).
    allowedHosts: true,
  },
})
