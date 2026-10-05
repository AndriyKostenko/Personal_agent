<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { marked } from 'marked'
import { markedHighlight } from 'marked-highlight'
import hljs from 'highlight.js/lib/common'
import DOMPurify from 'dompurify'
import BinaryPortrait from './components/BinaryPortrait.vue'
import FallingQuestions from './components/FallingQuestions.vue'

// в marked 18 highlight подключается через расширение, а не через setOptions
marked.use(markedHighlight({
  langPrefix: 'hljs language-',
  highlight(code, lang) {
    const language = hljs.getLanguage(lang) ? lang : 'plaintext'
    return hljs.highlight(code, { language }).value
  },
}))

// the backend address comes from the build environment (frontend/.env: VITE_API_BASE=https://host/api/v1)
const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000/api/v1'
const API_URL = `${API_BASE}/chat/stream`
const USAGE_URL = `${API_BASE}/usage`
// the images are served by the same backend
const MEDIA_ORIGIN = new URL(API_BASE, window.location.href).origin
const MEDIA_PATH = /^\/media\/[\w.-]+$/ // /media/<file name>: nothing else is ever loaded
const MODEL_LABEL = 'gpt-4o-mini'

// Pictures only from our backend: this closes the leak of data through ![](https://evil/?q=...).
// The model often "improves" the address it got from the tool (it adds a made-up domain to
// "/media/x.jpg"), so only the PATH is trusted: the address is rebuilt from it on our own origin.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'IMG') {
    let path = ''
    try {
      path = new URL(node.getAttribute('src') || '', window.location.href).pathname
    } catch {
      /* not a URL at all */
    }
    if (!MEDIA_PATH.test(path)) {
      node.parentNode?.removeChild(node)
      return
    }
    node.setAttribute('src', `${MEDIA_ORIGIN}${path}`) // host, query and fragment are dropped
    node.setAttribute('loading', 'lazy')
    node.setAttribute('referrerpolicy', 'no-referrer')
  }
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

// единственная точка, где Markdown превращается в HTML
const renderMarkdown = (md) =>
  DOMPurify.sanitize(marked.parse(md, { async: false }), { USE_PROFILES: { html: true } })

// crypto.randomUUID exists only in secure contexts (https / localhost); getRandomValues works on plain http too
const newId = () => {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const b = crypto.getRandomValues(new Uint8Array(16))
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

// Anonymous visitor id. The server counts the limits per id (and, as a safety net, per IP).
const CLIENT_KEY = 'about-andriy-client-id'
const getClientId = () => {
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
const clientId = getClientId()
const apiHeaders = { 'Content-Type': 'application/json', 'X-Client-Id': clientId }

// app states
const question = ref('')
const isLoading = ref(false)
const chatContainer = ref(null)
const steps = ref([]) // live steps of the agent for the current request
const streaming = ref(false) // true while the answer text is being typed out

// ───────────── usage limits (the server is the source of truth) ─────────────
const usage = ref({ questions_used: 0, questions_limit: 10 })
const questionsLeft = computed(() => Math.max(0, usage.value.questions_limit - usage.value.questions_used))
const serverNotice = ref('') // a limit message that came with a refusal of the server

const loadUsage = async () => {
  try {
    const response = await fetch(USAGE_URL, { headers: { 'X-Client-Id': clientId } })
    if (response.ok) usage.value = await response.json()
  } catch {
    /* the backend is not reachable: the defaults stay, the server checks anyway */
  }
}
onMounted(loadUsage)

// ───────────── the conversation ─────────────
// There is one conversation per visitor. Its id is also the thread_id of the agent's memory.
// It lives in localStorage, next to the visitor id: the limits are per visitor, so the
// conversation has to be reachable again after the tab was closed.
const STORE_KEY = 'about-andriy-chat'
const OLD_STORE_KEY = 'about-andriy-chats' // the earlier version kept a list of chats

const loadConversation = () => {
  try {
    let saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null')
    if (!saved) {
      // migrate: keep the active (or the longest) chat of the old list, so its thread stays known to the server
      const old = JSON.parse(localStorage.getItem(OLD_STORE_KEY) || 'null')
      const list = Array.isArray(old?.chats) ? old.chats : []
      saved =
        list.find((c) => c.id === old.activeId) ||
        [...list].sort((x, y) => y.messages.length - x.messages.length)[0] ||
        null
    }
    if (!saved || !Array.isArray(saved.messages)) return { id: newId(), messages: [] }
    for (const m of saved.messages) {
      if (m.role === 'assistant') {
        m.answer = renderMarkdown(m.raw || '') // HTML is never trusted from storage
        m.streaming = false
      }
    }
    return { id: saved.id, messages: saved.messages }
  } catch {
    return { id: newId(), messages: [] }
  }
}

const conversation = loadConversation()
const threadId = conversation.id
const messages = ref(conversation.messages)
const home = ref(!messages.value.length) // true = the main screen (the portrait) is shown
const showHero = computed(() => home.value || !messages.value.length)

// Why the visitor can not ask right now (null = can)
const blockReason = computed(() => (questionsLeft.value === 0 ? 'questions' : null))
const blockMessage = computed(() => {
  if (blockReason.value === 'questions') {
    return `QUESTION LIMIT REACHED: ALL ${usage.value.questions_limit} QUESTIONS ARE USED`
  }
  return serverNotice.value
})
const placeholder = computed(() =>
  blockReason.value === 'questions' ? 'No questions left' : "Ask me about Andriy or use the commands '/'...",
)

// saving is throttled: while an answer streams the messages change on every token
let saveTimer = null
const saveSession = () => {
  saveTimer = null
  try {
    const data = { id: threadId, messages: messages.value }
    // the rendered HTML ("answer") is not stored: it is rebuilt from the Markdown ("raw")
    localStorage.setItem(STORE_KEY, JSON.stringify(data, (k, v) => (k === 'answer' ? undefined : v)))
  } catch {
    /* storage is full or blocked: the conversation just lives until the page is closed */
  }
}
watch(messages, () => { if (!saveTimer) saveTimer = setTimeout(saveSession, 300) }, { deep: true })

const resetScroll = async (toBottom) => {
  await nextTick()
  if (chatContainer.value) chatContainer.value.scrollTop = toBottom ? chatContainer.value.scrollHeight : 0
}

// the main screen (the portrait); the conversation is kept and continues with the next question
const goHome = () => {
  if (isLoading.value) return
  home.value = true
  question.value = ''
  resetScroll(false)
}

// reads Server-Sent Events ("data: {...}\n\n") from a fetch response
async function* readEvents(response) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() // the last part may be incomplete
    for (const part of parts) {
      if (part.startsWith('data: ')) yield JSON.parse(part.slice(6))
    }
  }
}

const scrollToBottom = async () => {
  // Scroll to bottom after next tick to ensure content is rendered
  await nextTick()
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

// ready-made questions: one click sends the prompt to the agent
const quickPrompts = [
  {
    tag: '01 / PROFILE',
    label: 'About Andriy',
    hint: 'Who he is, where he is from',
    prompt: 'Tell me about Andriy: who he is, where he is from and what he is like.',
  },
  {
    tag: '02 / CAREER',
    label: 'His professional career',
    hint: 'Background, work, skills',
    prompt:
      "Describe Andriy's professional career: his background, his current work as a software engineer and the technical skills his notes show.",
  },
  {
    tag: '03 / LIFE',
    label: 'His personal life',
    hint: 'Family, pets, hobbies, photos',
    prompt: "Tell me about Andriy's personal life: family, pets and hobbies, and show his photos.",
  },
]

// Questions for the falling tabs on both sides of the screen; they follow the topics of the notes
const sideQuestions = [
  "Show me Andriy's photos",
  'How did Andriy win an AI hackathon?',
  'Show his AI hackathon certificate',
  'What does he know about async in Python?',
  'How does Python manage memory, according to his notes?',
  'What did he learn about the Python event loop?',
  'What does he know about multithreading and multiprocessing?',
  'What are the CPython internals he studied?',
  'How does he approach testing in Python?',
  'Does Andriy know Go? What about goroutines?',
  'Which design patterns has he studied?',
  'What did he learn about PostgreSQL isolation levels?',
  'What does he know about the N+1 problem?',
  'What does idempotency mean in his notes?',
  'What does he know about refresh tokens?',
  'What does he know about JavaScript closures?',
  'Tell me about his family and pets',
  'What are his hobbies?',
  'What technologies does Andriy work with?',
  'What is Andriy learning right now?',
]

// slash commands typed in the prompt field: a command is a shortcut for a ready prompt, the agent
// (and its find_photos tool) answers it like any other question
const commands = [
  {
    name: '/about',
    hint: 'Who Andriy is',
    prompt: 'Tell me about Andriy: who he is, where he is from and what he is like.',
  },
  {
    name: '/career',
    hint: 'His professional career',
    prompt:
      "Describe Andriy's professional career: his background, his current work as a software engineer and the technical skills his notes show.",
  },
  {
    name: '/skills',
    hint: 'What he knows',
    prompt:
      "Summarize Andriy's technical skills based on his notes: languages, backend topics, databases and anything else he has studied. Group them by area and include short code examples from the notes where they exist.",
  },
]

// the menu while the visitor is typing the command name ("/" or "/sho")
const suggestions = computed(() => {
  const typed = question.value.trimStart().toLowerCase()
  if (!typed.startsWith('/') || typed.includes(' ')) return []
  return commands.filter((c) => c.name.startsWith(typed))
})

const runCommand = (cmd) => sendQuestion(cmd.prompt, cmd.name)

const askMentor = () => {
  const typed = question.value.trim()
  if (!typed.startsWith('/')) return sendQuestion(typed)
  const lower = typed.toLowerCase()
  const cmd = commands.find((c) => c.name === lower) || (suggestions.value.length === 1 ? suggestions.value[0] : null)
  if (cmd) return runCommand(cmd)
  serverNotice.value = `UNKNOWN COMMAND. TRY ${commands.map((c) => c.name.toUpperCase()).join(', ')}`
}
const askQuickPrompt = (item) => sendQuestion(item.prompt)

const sendQuestion = async (text, shown = text) => {
  const userQuery = text.trim()
  if (!userQuery || isLoading.value || blockReason.value) return

  question.value = ''
  serverNotice.value = ''

  home.value = false
  const msgs = messages.value // the reactive proxy

  // Add user's message to UI
  msgs.push({ role: 'user', content: shown.trim() })
  isLoading.value = true
  steps.value = []
  scrollToBottom()

  let rejected = false
  try {
    const response = await fetch(API_URL, {
      method: 'POST',
      headers: apiHeaders,
      body: JSON.stringify({ message: userQuery, thread_id: threadId })
    })

    if (!response.ok) {
      const detail = (await response.json().catch(() => null))?.detail
      if (detail?.usage) usage.value = detail.usage
      if (response.status === 429 && detail?.message) {
        // refused before the model ran: the question is taken back, nothing was counted
        rejected = true
        serverNotice.value = detail.message
      }
      throw new Error('Server error')
    }
    if (!response.body) throw new Error('Server error')

    let answered = false
    let streamMsg = null // the assistant message that is being typed out
    let renderQueued = false

    // re-rendering Markdown on every token is wasteful: at most once per animation frame
    const renderStream = () => {
      if (renderQueued) return
      renderQueued = true
      requestAnimationFrame(() => {
        renderQueued = false
        if (streamMsg) {
          streamMsg.answer = renderMarkdown(streamMsg.raw)
          scrollToBottom()
        }
      })
    }

    for await (const event of readEvents(response)) {
      if (event.type === 'step') {
        steps.value.push(event.text)
        scrollToBottom()
      } else if (event.type === 'token') {
        if (!streamMsg) {
          // the first token: the live steps list turns into a collapsed one inside the message
          msgs.push({ role: 'assistant', raw: '', answer: '', streaming: true, steps: [...steps.value] })
          streamMsg = msgs[msgs.length - 1] // the reactive proxy
          streaming.value = true
        }
        streamMsg.raw += event.text
        renderStream()
      } else if (event.type === 'reset') {
        // the model wrote some text before calling a tool: dropping it
        if (streamMsg) msgs.pop()
        streamMsg = null
        streaming.value = false
      } else if (event.type === 'answer') {
        const html = renderMarkdown(event.answer) // the final, complete render
        if (streamMsg) {
          streamMsg.raw = event.answer
          streamMsg.answer = html
          streamMsg.streaming = false
        } else {
          // no tokens were streamed (e.g. a refusal)
          msgs.push({ role: 'assistant', raw: event.answer, answer: html, steps: [...steps.value] })
        }
        answered = true
      } else if (event.type === 'usage') {
        usage.value = event.usage // counted when accepted, given back when the answer failed
      } else if (event.type === 'error') {
        throw Object.assign(new Error(event.message), { userMessage: event.message })
      }
    }
    if (!answered) throw new Error('Empty response')
  } catch (error) {
    console.error('chat request failed:', error)
    if (rejected) {
      msgs.pop() // the refused question disappears from the chat
    } else {
      msgs.push({
        role: 'error',
        content: error.userMessage || 'Could not get a response. Please check your connection to the server.'
      })
    }
  } finally {
    // if the stream broke in the middle, the half-typed message stays but stops "typing"
    msgs.forEach((m) => { if (m.streaming) m.streaming = false })
    isLoading.value = false
    streaming.value = false
    steps.value = []
    scrollToBottom()
  }
}
</script>

<template>
  <div class="shell">
    <FallingQuestions
      :questions="sideQuestions"
      :disabled="isLoading || !!blockReason"
      @pick="sendQuestion"
    />

    <!-- Status bar -->
    <header class="topbar">
      <button
        class="brand"
        title="Back to the main screen"
        aria-label="Back to the main screen"
        :disabled="isLoading"
        @click="goHome"
      >
        <span class="brand-mark"></span>
        <span>ANDRIY <span class="dim">v2.0</span></span>
      </button>
      <div class="status">
        <span class="stat"><i class="dot"></i>LIVE</span>
        <span class="stat">QUESTIONS <b>{{ usage.questions_used }}/{{ usage.questions_limit }}</b></span>
        <span class="stat hide-sm">MODEL <b>{{ MODEL_LABEL }}</b></span>
        <span class="stat hide-sm">THREAD <b>{{ threadId.slice(0, 6) }}</b></span>
      </div>
    </header>

    <div class="body">
      <section class="main">
    <main class="chat-box" ref="chatContainer">
      <div class="column">
        <!-- Empty state: animated binary portrait + ready-made questions -->
        <section v-if="showHero" class="hero">
          <div class="portrait frame">
            <span class="cap cap-tl">[01] SUBJECT</span>
            <span class="cap cap-tr">ANDRIY.IMG</span>
            <BinaryPortrait src="/hero.jpg" />
            <span class="cap cap-bl">BINARY // DECODE</span>
            <span class="cap cap-br">CLICK TO REPLAY</span>
          </div>
          <h1>Andriy <span class="ver">v2.0</span></h1>
          <p class="role">SOFTWARE ENGINEER</p>
          <p class="lead">
            I'm AgentAndriy, Andriy's personal agent.
          </p>
          <div class="cards">
            <button
              v-for="item in quickPrompts"
              :key="item.label"
              class="card frame"
              :disabled="!!blockReason"
              @click="askQuickPrompt(item)"
            >
              <span class="arrow">↗</span>
              <span class="tag">{{ item.tag }}</span>
              <strong>{{ item.label }}</strong>
              <small>{{ item.hint }}</small>
            </button>
          </div>
          <p class="legal"><a href="/privacy">Privacy Policy</a></p>
        </section>

        <template v-if="!showHero">
        <!-- the same animated portrait, small, in the top left corner of the chat -->
        <div class="inline-portrait frame">
          <BinaryPortrait src="/hero.jpg" :cell-w="3.2" :cell-h="5" />
        </div>
        <div v-for="(msg, index) in messages" :key="index" :class="['row', msg.role]">
          <!-- User's message -->
          <div v-if="msg.role === 'user'" class="bubble user-bubble">
            {{ msg.content }}
          </div>

          <!-- Assistant's message -->
          <template v-else-if="msg.role === 'assistant'">
            <span class="avatar"><img src="/hero.jpg" alt="" /></span>
            <div class="bubble ai-bubble frame">
              <!-- Steps the agent took (collapsed) -->
              <details class="steps-done" v-if="msg.steps && msg.steps.length">
                <summary>Agent steps ({{ msg.steps.length }})</summary>
                <ul>
                  <li v-for="(s, i) in msg.steps" :key="i">
                    <span class="step-icon"></span>{{ s }}
                  </li>
                </ul>
              </details>

              <!-- Main answer: HTML is sanitized by DOMPurify in renderMarkdown() -->
              <div class="markdown-content" :class="{ streaming: msg.streaming }" v-html="msg.answer"></div>
            </div>
          </template>

          <!-- Error state -->
          <div v-else class="bubble error-bubble">
            {{ msg.content }}
          </div>
        </div>
        </template>

        <!-- Live steps while the agent works -->
        <div v-if="isLoading && !streaming" class="steps-live frame">
          <p class="steps-head"><span>[AGENT] SYSTEM STATUS</span><span>{{ steps.length }} STEPS</span></p>
          <div class="seg"><i v-for="n in 8" :key="n" :class="{ on: n <= steps.length }"></i></div>
          <TransitionGroup name="step" tag="ul">
            <li
              v-for="(s, i) in steps"
              :key="i"
              :class="{ active: i === steps.length - 1 }"
            >
              <span class="step-icon"></span>{{ s }}
            </li>
          </TransitionGroup>
          <div v-if="!steps.length" class="loading-indicator">CONNECTING...</div>
        </div>
      </div>
    </main>

    <div class="dock">
      <p v-if="blockMessage" class="notice">{{ blockMessage }}</p>

      <!-- How many questions were asked -->
      <div class="usage" :class="{ low: questionsLeft <= 1 }">
        <span>QUESTIONS</span>
        <span class="usage-seg" role="img" :aria-label="`${usage.questions_used} of ${usage.questions_limit} questions used`">
          <i v-for="n in usage.questions_limit" :key="n" :class="{ on: n <= usage.questions_used }"></i>
        </span>
        <b>{{ usage.questions_used }}/{{ usage.questions_limit }}</b>
      </div>

      <!-- Compact chips once the conversation has started -->
      <div v-if="!showHero" class="chips">
        <button
          v-for="item in quickPrompts"
          :key="item.label"
          class="chip"
          :disabled="isLoading || !!blockReason"
          @click="askQuickPrompt(item)"
        >
          {{ item.label }}
        </button>
      </div>

      <div class="composer-wrap">
      <div v-if="suggestions.length && !isLoading && !blockReason" class="slash frame" role="listbox">
        <button
          v-for="c in suggestions"
          :key="c.name"
          type="button"
          class="slash-item"
          role="option"
          @click="runCommand(c)"
        >
          <b>{{ c.name }}</b><span>{{ c.hint }}</span>
        </button>
      </div>
      <form class="composer frame" @submit.prevent="askMentor">
        <input
          v-model="question"
          type="text"
          :placeholder="placeholder"
          maxlength="1000"
          :disabled="isLoading || !!blockReason"
        />
        <button
          type="submit"
          class="send"
          title="Send"
          aria-label="Send"
          :disabled="isLoading || !!blockReason || !question.trim()"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
        </button>
      </form>
      </div>
      <p class="fineprint">ANSWERS ARE GENERATED FROM ANDRIY'S NOTES</p>
    </div>
      </section>
    </div>
  </div>
</template>

<style>
/* ───────────── tokens: black, warm greys, orange / red accents ───────────── */
:root {
  color-scheme: dark;
  --bg: #080808;
  --panel: rgba(13, 13, 13, 0.86);
  --ink: #cecece;
  --ink-strong: #f2f0ed;
  --muted: #afa9a3;
  --brown: #643d36;
  --red: #ef2007;
  --red-dk: #9f1f0d;
  --orange: #e5571c;
  --amber: #e5953a;
  --line: rgba(206, 206, 206, 0.16);
  --line-strong: rgba(206, 206, 206, 0.32);
  --display: 'Michroma', 'Space Grotesk', system-ui, sans-serif;
  --mono: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  --sans: 'Space Grotesk', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
}

* {
  box-sizing: border-box;
  scrollbar-width: thin;
  scrollbar-color: rgba(206, 206, 206, 0.3) transparent;
}

::selection {
  background: var(--orange);
  color: var(--bg);
}

body {
  margin: 0;
  font-family: var(--sans);
  color: var(--ink);
  background: var(--bg);
}

.shell {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  isolation: isolate;
}

.inline-portrait {
  align-self: flex-start;
  width: 124px;
  padding: 8px;
  background: rgba(13, 13, 13, 0.7);
  border: 1px solid var(--line-strong);
}

/* backdrop: a faint technical grid, a strong orange glow spreading from the top right corner across the screen, a dark red one bottom left, grain */
.shell::before {
  content: '';
  position: fixed;
  inset: 0;
  z-index: -2;
  background:
    radial-gradient(ellipse 95vw 95vh at 100% 0%, rgba(229, 87, 28, 0.5), rgba(229, 87, 28, 0.3) 30%, rgba(159, 31, 13, 0.15) 62%, transparent 100%),
    radial-gradient(700px 480px at -8% 110%, rgba(159, 31, 13, 0.2), transparent 66%),
    linear-gradient(rgba(206, 206, 206, 0.035) 1px, transparent 1px) 0 0 / 48px 48px,
    linear-gradient(90deg, rgba(206, 206, 206, 0.035) 1px, transparent 1px) 0 0 / 48px 48px,
    var(--bg);
}

.shell::after {
  content: '';
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  opacity: 0.06;
  mix-blend-mode: overlay;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");
}

/* thin frame with orange corner marks, as on the pitch-deck panels */
.frame {
  position: relative;
}

.frame::after {
  --t: 9px;
  --c: var(--orange);
  content: '';
  position: absolute;
  inset: -1px;
  pointer-events: none;
  background:
    linear-gradient(var(--c), var(--c)) top left / var(--t) 1px no-repeat,
    linear-gradient(var(--c), var(--c)) top left / 1px var(--t) no-repeat,
    linear-gradient(var(--c), var(--c)) top right / var(--t) 1px no-repeat,
    linear-gradient(var(--c), var(--c)) top right / 1px var(--t) no-repeat,
    linear-gradient(var(--c), var(--c)) bottom left / var(--t) 1px no-repeat,
    linear-gradient(var(--c), var(--c)) bottom left / 1px var(--t) no-repeat,
    linear-gradient(var(--c), var(--c)) bottom right / var(--t) 1px no-repeat,
    linear-gradient(var(--c), var(--c)) bottom right / 1px var(--t) no-repeat;
}

/* ───────────── status bar ───────────── */
.topbar {
  position: relative;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 24px;
  background: rgba(8, 8, 8, 0.8);
  border-bottom: 1px solid var(--line-strong);
  font-family: var(--mono);
  font-size: 0.66rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--muted);
}

/* the brand is a button: it brings you back to the main screen */
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
  font-family: var(--display);
  font-size: 0.78rem;
  letter-spacing: 0.14em;
  color: var(--ink-strong);
  background: none;
  border: none;
  cursor: pointer;
  transition: color 0.2s;
}

.brand:hover:not(:disabled) {
  color: #fff;
}

.brand:hover:not(:disabled) .brand-mark {
  background: var(--red);
}

.brand:disabled {
  cursor: default;
  opacity: 0.7;
}

.brand .dim {
  font-family: var(--mono);
  font-size: 0.66rem;
  letter-spacing: 0.08em;
  text-transform: none; /* "v2.0", not "V2.0" */
  color: var(--orange);
}

.brand-mark {
  width: 11px;
  height: 11px;
  background: var(--orange);
  clip-path: polygon(0 0, 100% 0, 100% 62%, 62% 100%, 0 100%);
  transition: background-color 0.2s;
}

.status {
  display: flex;
  align-items: center;
  gap: 22px;
}

.stat b {
  margin-left: 4px;
  font-weight: 700;
  color: var(--ink);
}

.dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 8px;
  background: var(--orange);
  animation: pulse 1.6s steps(2, end) infinite;
}

@keyframes pulse {
  50% {
    opacity: 0.25;
  }
}

/* ───────────── layout ───────────── */
.body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

/* ───────────── chat ───────────── */
.chat-box {
  flex: 1;
  overflow-y: auto;
}

.column {
  display: flex;
  flex-direction: column;
  gap: 18px;
  width: 100%;
  max-width: 780px;
  margin: 0 auto;
  padding: 24px 20px;
}

.legal {
  margin: 20px 0 0;
  font-family: var(--mono);
  font-size: 11px;
  text-align: center;
}

.legal a {
  color: var(--muted);
}

/* hero (empty state) */
.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.portrait {
  width: min(360px, 88vw, 46vh); /* the cards must fit on short screens too */
  padding: 26px 14px 28px;
  background: rgba(13, 13, 13, 0.7);
  border: 1px solid var(--line-strong);
}

.cap {
  position: absolute;
  font-family: var(--mono);
  font-size: 0.54rem;
  letter-spacing: 0.16em;
  color: var(--muted);
  pointer-events: none;
}

.cap-tl { top: 8px; left: 10px; color: var(--orange); }
.cap-tr { top: 8px; right: 10px; }
.cap-bl { bottom: 8px; left: 10px; }
.cap-br { bottom: 8px; right: 10px; }

.hero h1 {
  margin: 22px 0 0;
  font-family: var(--display);
  font-size: clamp(1.7rem, 5.4vw, 2.7rem);
  font-weight: 400;
  letter-spacing: 0.04em;
  line-height: 1.05;
  text-transform: uppercase;
  white-space: nowrap;
  color: var(--ink-strong);
}

.hero h1 .ver {
  font-family: var(--mono);
  font-size: 0.3em;
  letter-spacing: 0.08em;
  text-transform: none; /* "v2.0", not "V2.0" */
  color: var(--orange);
}

.role {
  margin: 14px 0 0;
  font-family: var(--mono);
  font-size: 0.68rem;
  letter-spacing: 0.22em;
  color: var(--orange);
}

.lead {
  max-width: 460px;
  margin: 14px 0 26px;
  line-height: 1.6;
  color: var(--muted);
}

.cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  width: 100%;
}

.card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  text-align: left;
  font: inherit;
  color: var(--ink);
  background: rgba(18, 18, 18, 0.75);
  border: 1px solid var(--line-strong);
  border-radius: 2px;
  cursor: pointer;
  transition: border-color 0.2s, background-color 0.2s, transform 0.2s;
}

.card:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.card:hover:not(:disabled) {
  background: rgba(229, 87, 28, 0.09);
  border-color: var(--orange);
  transform: translateY(-2px);
}

.card .tag {
  font-family: var(--mono);
  font-size: 0.6rem;
  letter-spacing: 0.18em;
  color: var(--orange);
}

.card strong {
  font-family: var(--display);
  font-size: 0.7rem;
  font-weight: 400;
  letter-spacing: 0.06em;
  line-height: 1.5;
  text-transform: uppercase;
  color: var(--ink-strong);
}

.card small {
  line-height: 1.45;
  color: var(--muted);
}

.card .arrow {
  position: absolute;
  top: 12px;
  right: 14px;
  font-family: var(--mono);
  color: var(--muted);
  transition: color 0.2s, transform 0.2s;
}

.card:hover:not(:disabled) .arrow {
  color: var(--orange);
  transform: translate(2px, -2px);
}

/* messages */
.row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.row.user {
  justify-content: flex-end;
}

.avatar {
  flex: none;
  width: 34px;
  height: 34px;
  margin-top: 2px;
  overflow: hidden;
  border: 1px solid var(--orange);
  border-radius: 2px;
}

.avatar img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: 50% 4%; /* the face is in the upper part of the photo */
  filter: grayscale(1) sepia(1) hue-rotate(-18deg) saturate(3) brightness(0.92);
}

.bubble {
  line-height: 1.6;
}

/* the light panel of the reference: light grey, dark text, one cut corner */
.user-bubble {
  max-width: 80%;
  padding: 11px 20px 11px 16px;
  font-weight: 500;
  color: var(--bg);
  background: var(--ink);
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 12px), calc(100% - 12px) 100%, 0 100%);
}

.ai-bubble {
  flex: 1;
  min-width: 0;
  padding: 16px 20px;
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: 2px;
  backdrop-filter: blur(10px);
}

.error-bubble {
  padding: 12px 18px;
  color: #ffb9ad;
  background: rgba(239, 32, 7, 0.08);
  border: 1px solid var(--red);
  border-radius: 2px;
}

/* ───────────── markdown ───────────── */
.markdown-content > :first-child {
  margin-top: 0;
}

.markdown-content > :last-child {
  margin-bottom: 0;
}

.markdown-content h1,
.markdown-content h2 {
  margin: 1.2em 0 0.6em;
  font-family: var(--display);
  font-size: 0.88rem;
  font-weight: 400;
  letter-spacing: 0.06em;
  line-height: 1.45;
  text-transform: uppercase;
  color: var(--orange);
}

.markdown-content h3 {
  margin: 1.1em 0 0.5em;
  font-family: var(--mono);
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--amber);
}

.markdown-content strong {
  color: var(--ink-strong);
}

.markdown-content a {
  color: var(--orange);
  text-decoration-color: rgba(229, 87, 28, 0.45);
}

.markdown-content li::marker {
  color: var(--orange);
}

.markdown-content table {
  width: 100%;
  margin: 10px 0;
  border-collapse: collapse;
  font-size: 0.9rem;
}

.markdown-content th,
.markdown-content td {
  padding: 6px 10px;
  text-align: left;
  border: 1px solid var(--line-strong);
}

.markdown-content th {
  font-family: var(--mono);
  font-size: 0.74rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--orange);
  background: rgba(229, 87, 28, 0.08);
}

.markdown-content img {
  display: block;
  max-width: 100%;
  max-height: 320px;
  margin: 10px 0;
  object-fit: cover;
  border: 1px solid var(--line-strong);
  border-radius: 2px;
  transition: border-color 0.2s;
}

.markdown-content img:hover {
  border-color: var(--orange);
}

.markdown-content pre {
  padding: 12px 14px;
  overflow-x: auto;
  background: #050505;
  border: 1px solid var(--line-strong);
  border-left: 2px solid var(--orange);
  border-radius: 2px;
}

.markdown-content code {
  font-family: var(--mono);
  font-size: 0.86em;
}

.markdown-content :not(pre) > code {
  padding: 2px 6px;
  color: var(--amber);
  background: rgba(229, 149, 58, 0.1);
  border-radius: 2px;
}

.markdown-content pre code {
  padding: 0;
  color: var(--ink);
  background: transparent;
}

/* code colours from the palette (highlight.js tokens) */
.hljs-keyword,
.hljs-selector-tag,
.hljs-doctag,
.hljs-meta .hljs-keyword {
  color: var(--orange);
}

.hljs-string,
.hljs-regexp,
.hljs-template-variable,
.hljs-addition {
  color: var(--amber);
}

.hljs-number,
.hljs-literal,
.hljs-symbol,
.hljs-bullet,
.hljs-deletion {
  color: #ff6a4d;
}

.hljs-built_in,
.hljs-type,
.hljs-class .hljs-title,
.hljs-title.class_ {
  color: #e8b878;
}

.hljs-title,
.hljs-title.function_,
.hljs-section {
  color: var(--ink-strong);
}

.hljs-attr,
.hljs-attribute,
.hljs-variable,
.hljs-property {
  color: var(--ink);
}

.hljs-comment,
.hljs-quote {
  font-style: italic;
  color: #7d7873;
}

.hljs-meta,
.hljs-params,
.hljs-subst {
  color: var(--muted);
}

/* typing cursor while the answer streams */
.markdown-content.streaming > :last-child::after {
  content: '█';
  margin-left: 3px;
  color: var(--orange);
  animation: blink 0.9s steps(2, start) infinite;
}

@keyframes blink {
  to {
    visibility: hidden;
  }
}

/* ───────────── agent steps ───────────── */
.steps-live ul,
.steps-done ul {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.steps-live {
  align-self: flex-start;
  min-width: 280px;
  margin-left: 46px;
  padding: 12px 16px 14px;
  background: var(--panel);
  border: 1px solid var(--line-strong);
}

.steps-head {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  margin: 0 0 8px;
  font-family: var(--mono);
  font-size: 0.58rem;
  letter-spacing: 0.18em;
  color: var(--muted);
}

/* the segmented bar of the reference: one block per step */
.seg {
  display: flex;
  gap: 3px;
  margin-bottom: 12px;
}

.seg i {
  flex: 1;
  height: 8px;
  background: rgba(206, 206, 206, 0.12);
  transition: background-color 0.3s;
}

.seg i.on {
  background: var(--orange);
}

.seg i.on:nth-last-child(n + 1):last-of-type {
  background: var(--orange);
}

.steps-live li,
.steps-done li {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: var(--mono);
  font-size: 0.74rem;
  color: var(--muted);
}

.steps-live li.active {
  color: var(--ink-strong);
}

.step-icon {
  position: relative;
  flex: none;
  width: 10px;
  height: 10px;
  background: var(--orange);
}

.steps-live li.active .step-icon {
  background: transparent; /* the current step: a rotating square frame */
  border: 2px solid var(--orange);
  border-top-color: transparent;
  animation: spin 0.8s linear infinite;
}

.steps-done {
  margin-bottom: 12px;
  padding: 8px 12px;
  background: rgba(0, 0, 0, 0.35);
  border: 1px solid var(--line);
}

.steps-done summary {
  cursor: pointer;
  font-family: var(--mono);
  font-size: 0.64rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--muted);
  outline: none;
}

.steps-done summary:hover {
  color: var(--orange);
}

.steps-done ul {
  margin-top: 8px;
}

.step-enter-active {
  transition: all 0.3s ease;
}

.step-enter-from {
  opacity: 0;
  transform: translateX(-10px);
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.loading-indicator {
  font-family: var(--mono);
  font-size: 0.72rem;
  letter-spacing: 0.16em;
  color: var(--muted);
  animation: pulse 1.2s steps(2, end) infinite;
}

/* ───────────── dock: chips + composer ───────────── */
.dock {
  width: 100%;
  max-width: 780px;
  margin: 0 auto;
  padding: 0 20px 14px;
}

/* the question counter above the prompt field */
.composer-wrap {
  position: relative;
}

/* the command menu opens above the prompt field */
.slash {
  position: absolute;
  right: 0;
  bottom: 100%;
  left: 0;
  z-index: 10;
  margin-bottom: 8px;
  background: rgba(13, 13, 13, 0.96);
  border: 1px solid var(--line-strong);
}

.slash-item {
  display: flex;
  align-items: baseline;
  gap: 14px;
  width: 100%;
  padding: 10px 14px;
  text-align: left;
  font-family: var(--mono);
  font-size: 0.72rem;
  color: var(--muted);
  background: none;
  border: none;
  cursor: pointer;
}

.slash-item b {
  color: var(--orange);
  letter-spacing: 0.06em;
}

.slash-item:hover {
  background: rgba(229, 87, 28, 0.1);
  color: var(--ink-strong);
}

.usage {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 0 2px 10px;
  font-family: var(--mono);
  font-size: 0.6rem;
  letter-spacing: 0.16em;
  color: var(--muted);
}

.usage b {
  font-weight: 700;
  color: var(--ink);
}

.usage-seg {
  display: flex;
  gap: 3px;
}

.usage-seg i {
  width: 22px;
  height: 7px;
  background: rgba(206, 206, 206, 0.14);
  transition: background-color 0.3s;
}

.usage-seg i.on {
  background: var(--orange);
}

.usage.low .usage-seg i.on {
  background: var(--red); /* the last question or none left */
}

.notice {
  margin: 0 0 10px;
  padding: 9px 12px;
  font-family: var(--mono);
  font-size: 0.62rem;
  letter-spacing: 0.12em;
  line-height: 1.5;
  color: #ffb9ad;
  background: rgba(239, 32, 7, 0.08);
  border: 1px solid var(--red);
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-bottom: 12px;
}

.chip {
  padding: 6px 12px;
  font-family: var(--mono);
  font-size: 0.64rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
  background: rgba(13, 13, 13, 0.7);
  border: 1px solid var(--line-strong);
  border-radius: 2px;
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s, background-color 0.2s;
}

.chip:hover:not(:disabled) {
  color: var(--orange);
  background: rgba(229, 87, 28, 0.08);
  border-color: var(--orange);
}

.chip:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* the light panel as the prompt field, with an orange square send button */
.composer {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px;
  background: var(--ink);
  border: 1px solid var(--ink);
  border-radius: 3px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  transition: box-shadow 0.2s;
}

.composer:focus-within {
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5), 0 0 0 1px var(--orange), 0 0 30px rgba(229, 87, 28, 0.28);
}

.composer input {
  flex: 1;
  min-width: 0;
  padding: 10px 6px 10px 14px;
  font: inherit;
  font-size: 1rem;
  color: var(--bg);
  background: transparent;
  border: none;
  outline: none;
}

.composer input::placeholder {
  color: #6b665f;
}

.composer input:disabled {
  cursor: not-allowed;
}

.send {
  display: grid;
  flex: none;
  place-items: center;
  width: 40px;
  height: 40px;
  padding: 0;
  color: var(--bg);
  background: var(--orange);
  border: none;
  border-radius: 2px;
  cursor: pointer;
  transition: background-color 0.2s, opacity 0.2s;
}

.send:hover:not(:disabled) {
  background: var(--red);
}

.send:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.fineprint {
  margin: 10px 0 0;
  text-align: center;
  font-family: var(--mono);
  font-size: 0.58rem;
  letter-spacing: 0.2em;
  color: var(--muted);
  opacity: 0.7;
}

/* ───────────── small screens & reduced motion ───────────── */
@media (max-width: 640px) {
  .topbar { padding: 12px 16px; }
  .hide-sm { display: none; }
  .cards { grid-template-columns: 1fr; }
  .column { padding: 16px 14px; }
  .dock { padding: 0 14px 12px; }
  .user-bubble { max-width: 90%; }
  .steps-live { min-width: 0; margin-left: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .dot,
  .loading-indicator {
    animation: none;
  }
  .step-enter-active {
    transition: none;
  }
  .steps-live li.active .step-icon {
    animation-duration: 3s;
  }
}
</style>
