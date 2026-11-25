import type { StreamBuffer } from './lib/streamBuffer'

export interface Usage {
  questions_used: number
  questions_limit: number
  booking_enabled?: boolean
}

export interface UserMessage {
  id: string
  role: 'user'
  content: string
}

export interface AssistantMessage {
  id: string
  role: 'assistant'
  /** the Markdown of the answer (the final one, or what had arrived when the stream stopped) */
  raw: string
  steps: string[]
  /** the stream was cut off (Stop button or a broken connection): the text is partial */
  incomplete?: boolean
  /** the server's id of this answer (also the LangSmith trace id); needed to rate it */
  runId?: string
  /** the visitor's rating: 1 = helpful, 0 = not helpful (one vote per answer) */
  feedback?: FeedbackScore
  /** only while the answer is streaming; never saved */
  live?: StreamBuffer
}

export type FeedbackScore = 0 | 1

export interface ErrorMessage {
  id: string
  role: 'error'
  content: string
}

export type Message = UserMessage | AssistantMessage | ErrorMessage

/** the events of POST /chat/stream (backend/agent/agent.py: astream_steps, routes.py: chat_stream) */
export type ChatEvent =
  | { type: 'step'; text: string }
  | { type: 'token'; text: string }
  | { type: 'reset' }
  | { type: 'answer'; answer: string; run_id?: string }
  | { type: 'usage'; usage: Usage }
  | { type: 'error'; message: string }

export interface Prompt {
  tag: string
  label: string
  hint: string
  prompt: string
}

export interface Command {
  name: string
  hint: string
  prompt: string
}
