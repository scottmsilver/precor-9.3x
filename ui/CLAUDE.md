# UI

## Build

```bash
cd ui && npx vite build    # outputs to ../static/
```

Build produces hashed filenames in `static/assets/` (gitignored). `static/index.html` is tracked.

## Stack

- React 19 + TypeScript
- Vite (build tool)
- No component library — custom components with CSS modules

## Directory Structure

| Directory | Purpose |
|-----------|---------|
| `routes/` | Page components: `Lobby.tsx`, `Running.tsx`, `Debug.tsx` |
| `components/` | Reusable UI components (ElevationProfile, buttons, etc.) |
| `state/` | TreadmillContext + useReducer, API helpers, WebSocket |
| `voice/` | `GeminiLiveClient.ts` (WebSocket), `useVoice.ts` (lifecycle hook) |
| `styles/` | Global styles and CSS |

## State Management

Single WebSocket connection managed by `TreadmillContext` with `useReducer`. All treadmill state flows through this context.

## Key Principles

- **No business logic in UI** — server decides everything. UI calls API endpoints and renders results.
- **Safety first** — stop button always visible when belt is moving.
- **Mobile/tablet first** — touch targets 44px+, no hover-dependent interactions.

## Voice

- `GeminiLiveClient.ts`: Direct WebSocket to Gemini Live API for real-time voice
- `useVoice.ts`: React hook managing voice session lifecycle (mic permissions, connect/disconnect, fallback)
- Voice requires HTTPS or Chrome's `--unsafely-treat-insecure-origin-as-secure` flag for `getUserMedia`
