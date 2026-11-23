# frontend

React 19 + Vite + TypeScript chat UI for AgentAndriy.

```sh
npm install
npm run dev        # http://localhost:5173, API from VITE_API_BASE (default http://127.0.0.1:8000/api/v1)
npm run build      # type-check + production build
npm run type-check
```

## Layout

- `src/hooks/useChat.ts`: the conversation, the SSE request to `/chat/stream`, Stop, limits, saving to localStorage
- `src/lib/sse.ts`: SSE frame parser over `fetch` (POST with a JSON body, which EventSource can't send)
- `src/lib/streamBuffer.ts`: incoming tokens go here, not into React state
- `src/hooks/useSmoothText.ts`: reveals the streamed text once per animation frame at an adaptive speed
- `src/components/Markdown.tsx` + `src/lib/markdown.ts`: Markdown split into memoised blocks; only the last block re-renders while streaming; DOMPurify + image/link hook
- `src/hooks/useStickToBottom.ts`: auto-scroll only while the reader is at the bottom
- `src/lib/binaryPortrait.ts`: the canvas portrait (framework-free)

## How streaming is rendered

1. Tokens are appended to a `StreamBuffer` (no re-render per token).
2. `useSmoothText` wakes on new text and moves the visible text towards it once per `requestAnimationFrame`,
   at `max(90, backlog / 0.25s)` chars/s: steady ~250 ms lag, bursts spread over several frames, no idle frames.
3. `Markdown` lexes the text into top-level blocks; finished blocks are `memo`ised (parsed, sanitised and
   highlighted once), only the tail block is rewritten, without syntax highlighting and with half-typed
   links / emphasis markers hidden.
4. Stop aborts the `fetch`; the server sees the disconnect and cancels the LLM call.
