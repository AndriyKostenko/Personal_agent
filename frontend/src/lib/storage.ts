import type { Message } from '../types'

// crypto.randomUUID exists only in secure contexts (https / localhost); getRandomValues works on plain http too
export function newId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const b = crypto.getRandomValues(new Uint8Array(16))
  b[6] = (b[6]! & 0x0f) | 0x40
  b[8] = (b[8]! & 0x3f) | 0x80
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

// Anonymous visitor id. The server counts the limits per id (and, as a safety net, per IP).
const CLIENT_KEY = 'about-andriy-client-id'

export function getClientId(): string {
  try {
    let id = localStorage.getItem(CLIENT_KEY)
    if (!id) {
      id = newId()
      localStorage.setItem(CLIENT_KEY, id)
    }
    return id
  } catch {
    return newId() // storage is blocked: the id lives until the page is closed
  }
}

// ───────────── the conversation ─────────────
// There is one conversation per visitor. Its id is also the thread_id of the agent's memory.
// It lives in localStorage, next to the visitor id: the limits are per visitor, so the
// conversation has to be reachable again after the tab was closed.
const STORE_KEY = 'about-andriy-chat'
const OLD_STORE_KEY = 'about-andriy-chats' // an earlier version kept a list of chats

export interface Conversation {
  id: string
  messages: Message[]
}

interface StoredMessage {
  id?: string
  role: Message['role']
  content?: string
  raw?: string
  steps?: string[]
  incomplete?: boolean
  runId?: string
  feedback?: number
}

function fromStored(m: StoredMessage): Message | null {
  const id = m.id || newId()
  if (m.role === 'assistant') {
    return {
      id,
      role: 'assistant',
      raw: String(m.raw ?? ''),
      steps: Array.isArray(m.steps) ? m.steps : [],
      incomplete: !!m.incomplete,
      // kept across reloads, so a rated answer stays rated (the server refuses a second vote anyway)
      runId: typeof m.runId === 'string' ? m.runId : undefined,
      feedback: m.feedback === 0 || m.feedback === 1 ? m.feedback : undefined,
    }
  }
  if (m.role === 'user' || m.role === 'error') return { id, role: m.role, content: String(m.content ?? '') }
  return null
}

export function loadConversation(): Conversation {
  try {
    let saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null')
    if (!saved) {
      // migrate: keep the active (or the longest) chat of the old list, so its thread stays known to the server
      const old = JSON.parse(localStorage.getItem(OLD_STORE_KEY) || 'null')
      const list: { id: string; messages: unknown[] }[] = Array.isArray(old?.chats) ? old.chats : []
      saved =
        list.find((c) => c.id === old.activeId) ||
        [...list].sort((x, y) => y.messages.length - x.messages.length)[0] ||
        null
    }
    if (!saved || typeof saved.id !== 'string' || !Array.isArray(saved.messages)) return { id: newId(), messages: [] }
    // only the Markdown is stored and trusted: HTML is always rebuilt (and sanitised) from it
    const messages = (saved.messages as StoredMessage[]).map(fromStored).filter((m): m is Message => m !== null)
    return { id: saved.id, messages }
  } catch {
    return { id: newId(), messages: [] }
  }
}

export function saveConversation(conversation: Conversation) {
  const messages: StoredMessage[] = conversation.messages.map((m) =>
    m.role === 'assistant'
      ? // a streaming answer is saved with the text that has arrived so far
        m.live && !m.live.done
        ? { id: m.id, role: m.role, raw: m.live.text, steps: m.steps, incomplete: true }
        : {
            id: m.id,
            role: m.role,
            raw: m.raw,
            steps: m.steps,
            incomplete: m.incomplete,
            runId: m.runId,
            feedback: m.feedback,
          }
      : { id: m.id, role: m.role, content: m.content },
  )
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({ id: conversation.id, messages }))
  } catch {
    /* storage is full or blocked: the conversation just lives until the page is closed */
  }
}
