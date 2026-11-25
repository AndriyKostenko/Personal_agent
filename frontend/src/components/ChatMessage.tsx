import { memo } from 'react'

import { useSmoothText } from '../hooks/useSmoothText'
import type { AssistantMessage, FeedbackScore, Message } from '../types'
import { Markdown } from './Markdown'

export type OnFeedback = (messageId: string, runId: string, score: FeedbackScore) => void

const VOTES: { score: FeedbackScore; label: string; title: string }[] = [
  { score: 1, label: '▲ HELPFUL', title: 'This answer was helpful' },
  { score: 0, label: '▼ NOT HELPFUL', title: 'This answer was not helpful' },
]

function Feedback({ msg, runId, onFeedback }: { msg: AssistantMessage; runId: string; onFeedback: OnFeedback }) {
  const voted = msg.feedback !== undefined
  return (
    <div className="feedback" role="group" aria-label="Rate this answer">
      {VOTES.map((v) => (
        <button
          key={v.score}
          type="button"
          className={msg.feedback === v.score ? 'vote chosen' : 'vote'}
          title={v.title}
          aria-pressed={msg.feedback === v.score}
          disabled={voted} // one vote per answer: the server refuses a second one anyway
          onClick={() => onFeedback(msg.id, runId, v.score)}
        >
          {v.label}
        </button>
      ))}
      {voted && <span className="thanks">THANKS</span>}
    </div>
  )
}

function AssistantBubble({ msg, onFeedback }: { msg: AssistantMessage; onFeedback?: OnFeedback }) {
  // a stored answer is shown at once; a live one is revealed smoothly, frame by frame
  const { text, settled } = useSmoothText(msg.live, msg.raw)
  // rate only a complete answer the server knows (old saved answers have no runId)
  const canRate = settled && !msg.incomplete && !!msg.runId && !!onFeedback
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
        {canRate && <Feedback msg={msg} runId={msg.runId!} onFeedback={onFeedback!} />}
      </div>
    </div>
  )
}

// memo: a new token or message re-renders only the message that changed, not the whole history
// (so onFeedback must be a stable function: useCallback in useChat)
export const ChatMessage = memo(function ChatMessage({ msg, onFeedback }: { msg: Message; onFeedback?: OnFeedback }) {
  if (msg.role === 'assistant') return <AssistantBubble msg={msg} onFeedback={onFeedback} />
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
