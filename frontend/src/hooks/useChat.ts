import { useCallback, useEffect, useRef, useState } from 'react'

import { CHAT_URL, DEFAULT_QUESTION_LIMIT, USAGE_URL } from '../config'
import { readEvents } from '../lib/sse'
import { getClientId, loadConversation, newId, saveConversation } from '../lib/storage'
import { StreamBuffer } from '../lib/streamBuffer'
import type { AssistantMessage, Message, Usage } from '../types'

const FAILED_MESSAGE = 'Could not get a response. Please check your connection to the server.'

const clientId = getClientId()
const initial = loadConversation()

class UserFacingError extends Error {}

export function useChat() {
  const threadId = initial.id
  const [messages, setMessages] = useState<Message[]>(initial.messages)
  const [steps, setSteps] = useState<string[]>([]) // live steps of the agent for the current request
  const [isLoading, setIsLoading] = useState(false)
  const [streaming, setStreaming] = useState(false) // the answer text has started to arrive
  // usage limits: the server is the source of truth
  const [usage, setUsage] = useState<Usage>({ questions_used: 0, questions_limit: DEFAULT_QUESTION_LIMIT })
  const [bookingEnabled, setBookingEnabled] = useState(false) // the server has the booking tools
  const [serverNotice, setServerNotice] = useState('') // a limit message that came with a refusal

  const busy = useRef(false) // isLoading for the event handlers (state is one render late)
  const abort = useRef<AbortController | null>(null)
  const latest = useRef(messages)
  latest.current = messages

  useEffect(() => {
    const controller = new AbortController()
    fetch(USAGE_URL, { headers: { 'X-Client-Id': clientId }, signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: Usage | null) => {
        if (!data) return
        setBookingEnabled(!!data.booking_enabled)
        setUsage(data)
      })
      .catch(() => {
        /* the backend is not reachable: the defaults stay, the server checks anyway */
      })
    return () => controller.abort()
  }, [])

  // Saving: debounced on every change of the list, plus when the page is hidden or closed (the
  // tokens of a streaming answer do not change the list, so they are picked up there).
  useEffect(() => {
    const timer = setTimeout(() => saveConversation({ id: threadId, messages }), 300)
    return () => clearTimeout(timer)
  }, [messages, threadId])
  useEffect(() => {
    const save = () => saveConversation({ id: threadId, messages: latest.current })
    window.addEventListener('pagehide', save)
    return () => window.removeEventListener('pagehide', save)
  }, [threadId])

  // closing the page or unmounting stops the request (and, on the server, the LLM call)
  useEffect(() => () => abort.current?.abort(), [])

  const patch = (id: string, change: Partial<AssistantMessage>) =>
    setMessages((list) => list.map((m) => (m.id === id && m.role === 'assistant' ? { ...m, ...change } : m)))

  const send = useCallback(
    async (text: string, shown: string = text) => {
      const query = text.trim()
      if (!query || busy.current) return
      busy.current = true

      const controller = new AbortController()
      abort.current = controller
      const userMsg: Message = { id: newId(), role: 'user', content: shown.trim() }
      setServerNotice('')
      setMessages((list) => [...list, userMsg])
      setIsLoading(true)
      setSteps([])

      const doneSteps: string[] = []
      let answerId = ''
      let buffer: StreamBuffer | null = null
      let rejected = false
      try {
        const response = await fetch(CHAT_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Client-Id': clientId },
          body: JSON.stringify({ message: query, thread_id: threadId }),
          signal: controller.signal,
        })
        if (!response.ok) {
          const detail = (await response.json().catch(() => null))?.detail
          if (detail?.usage) setUsage(detail.usage)
          if (response.status === 429 && detail?.message) {
            // refused before the model ran: the question is taken back, nothing was counted
            rejected = true
            setServerNotice(detail.message)
          }
          throw new Error(`HTTP ${response.status}`)
        }
        if (!response.body) throw new Error('no response body')

        let answered = false
        for await (const event of readEvents(response.body)) {
          switch (event.type) {
            case 'step':
              doneSteps.push(event.text)
              setSteps([...doneSteps])
              break
            case 'token':
              if (!buffer) {
                // the first token: the live steps panel turns into a collapsed list inside the message
                const live = new StreamBuffer()
                buffer = live
                answerId = newId()
                const msg: AssistantMessage = { id: answerId, role: 'assistant', raw: '', steps: [...doneSteps], live }
                setMessages((list) => [...list, msg])
                setStreaming(true)
              }
              // no React state here: the message component reveals the text at its own pace
              buffer.append(event.text)
              break
            case 'reset': {
              // the model wrote some text before calling a tool: dropping it
              if (buffer) {
                const dropped = answerId
                setMessages((list) => list.filter((m) => m.id !== dropped))
              }
              buffer = null
              setStreaming(false)
              break
            }
            case 'answer':
              if (buffer) {
                buffer.finish(event.answer)
                patch(answerId, { raw: event.answer })
              } else {
                // no tokens were streamed (e.g. a refusal)
                const msg: AssistantMessage = { id: newId(), role: 'assistant', raw: event.answer, steps: [...doneSteps] }
                setMessages((list) => [...list, msg])
              }
              answered = true
              break
            case 'usage':
              setUsage(event.usage) // counted when accepted, given back when the answer failed
              break
            case 'error':
              throw new UserFacingError(event.message)
          }
        }
        if (!answered) throw new Error('the stream ended without an answer')
      } catch (error) {
        if (rejected) {
          setMessages((list) => list.filter((m) => m.id !== userMsg.id)) // the refused question disappears
        } else if (!controller.signal.aborted) {
          console.error('chat request failed:', error)
          const content = error instanceof UserFacingError ? error.message : FAILED_MESSAGE
          setMessages((list) => [...list, { id: newId(), role: 'error', content }])
        }
        // stopped by the visitor: no error, the partial answer stays (marked as incomplete below)
      } finally {
        const cut: StreamBuffer | null = buffer
        if (cut && !cut.done) {
          // the stream broke or was stopped in the middle: the half-typed answer stays
          cut.finish()
          patch(answerId, { raw: cut.text, incomplete: true })
        }
        if (abort.current === controller) abort.current = null
        busy.current = false
        setIsLoading(false)
        setStreaming(false)
        setSteps([])
      }
    },
    [threadId],
  )

  /** the Stop button: closing the connection makes the server cancel the LLM call */
  const stop = useCallback(() => abort.current?.abort(), [])

  return {
    threadId,
    messages,
    steps,
    isLoading,
    streaming,
    usage,
    bookingEnabled,
    serverNotice,
    setServerNotice,
    send,
    stop,
  }
}
