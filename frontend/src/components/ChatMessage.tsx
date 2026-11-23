import { memo } from 'react'

import { useSmoothText } from '../hooks/useSmoothText'
import type { AssistantMessage, Message } from '../types'
import { Markdown } from './Markdown'

function AssistantBubble({ msg }: { msg: AssistantMessage }) {
  // a stored answer is shown at once; a live one is revealed smoothly, frame by frame
  const { text, settled } = useSmoothText(msg.live, msg.raw)
  return (
    <div className="row assistant">
      <span className="avatar">
        <img src="/hero.jpg" alt="" />
      </span>
      {/* aria-busy: screen readers wait for the whole answer instead of reading every frame */}
      <div className="bubble ai-bubble frame" aria-busy={!settled}>
        {msg.steps.length > 0 && (
          <details className="steps-done">
            <summary>Agent steps ({msg.steps.length})</summary>
            <ul>
              {msg.steps.map((s, i) => (
                <li key={i}>
                  <span className="step-icon" />
                  {s}
                </li>
              ))}
            </ul>
          </details>
        )}
        <Markdown text={text} streaming={!settled} animate={!!msg.live} />
        {msg.incomplete && settled && <p className="incomplete">[STOPPED] THE ANSWER IS INCOMPLETE</p>}
      </div>
    </div>
  )
}

// memo: a new token or message re-renders only the message that changed, not the whole history
export const ChatMessage = memo(function ChatMessage({ msg }: { msg: Message }) {
  if (msg.role === 'assistant') return <AssistantBubble msg={msg} />
  if (msg.role === 'user') {
    return (
      <div className="row user">
        <div className="bubble user-bubble">{msg.content}</div>
      </div>
    )
  }
  return (
    <div className="row error">
      <div className="bubble error-bubble" role="alert">
        {msg.content}
      </div>
    </div>
  )
})
