// Issue #40: backend base URLs, shared by client.ts/sessionClient.ts/
// SceneImagePanel.tsx/portraits.ts - all four hardcoded 'http(s)://
// localhost:8000' independently before this.
//
// API_BASE_URL defaults to '' (same-origin, relative requests) rather than
// an absolute localhost URL - that's what makes this work unmodified both
// through a single tunneled origin (see vite.config.ts's dev-server proxy,
// which makes plain `npm run dev` behave the same way) and if the built
// frontend is ever served directly by the FastAPI backend itself. An
// explicit VITE_API_BASE_URL overrides this for pointing at a backend on a
// genuinely different origin (e.g. local dev without the Vite proxy).
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''

// WebSocket can't reliably resolve a relative URL the way fetch() does, so
// this is built explicitly from the current page's own origin - upgrading
// to wss:// under https: (browsers block a plain ws:// connection from an
// https: page as mixed content) - rather than left as a bare relative path.
export const WS_BASE_URL: string =
  import.meta.env.VITE_WS_BASE_URL ??
  `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`
