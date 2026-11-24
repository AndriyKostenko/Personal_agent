import type { Command, Prompt } from './types'

// the backend address comes from the build environment (frontend/.env: VITE_API_BASE=https://host/api/v1)
export const API_BASE: string = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000/api/v1'
export const CHAT_URL = `${API_BASE}/chat/stream`
export const USAGE_URL = `${API_BASE}/usage`
export const FEEDBACK_URL = `${API_BASE}/feedback`
// the images are served by the same backend
export const MEDIA_ORIGIN = new URL(API_BASE, window.location.href).origin
export const MODEL_LABEL = 'gpt-4o-mini'
export const DEFAULT_QUESTION_LIMIT = 10

export const CALL_PROMPT = 'I would like to book a call with Andriy. What times are available?'

const ABOUT_PROMPT = 'Tell me about Andriy: who he is, where he is from and what he is like.'
const CAREER_PROMPT =
  "Describe Andriy's professional career: his background, his current work as a software engineer and the technical skills his notes show."

// ready-made questions: one click sends the prompt to the agent
export const QUICK_PROMPTS: Prompt[] = [
  { tag: '01 / PROFILE', label: 'About Andriy', hint: 'Who he is, where he is from', prompt: ABOUT_PROMPT },
  { tag: '02 / CAREER', label: 'His professional career', hint: 'Background, work, skills', prompt: CAREER_PROMPT },
  {
    tag: '03 / LIFE',
    label: 'His personal life',
    hint: 'Family, pets, hobbies, photos',
    prompt: "Tell me about Andriy's personal life: family, pets and hobbies, and show his photos.",
  },
]
export const CALL_CARD: Prompt = { tag: '04 / CALL', label: 'Book a call', hint: 'Pick a free time slot', prompt: CALL_PROMPT }

// slash commands typed in the prompt field: a command is a shortcut for a ready prompt, the agent
// (and its find_photos tool) answers it like any other question
export const COMMANDS: Command[] = [
  { name: '/about', hint: 'Who Andriy is', prompt: ABOUT_PROMPT },
  { name: '/career', hint: 'His professional career', prompt: CAREER_PROMPT },
  {
    name: '/skills',
    hint: 'What he knows',
    prompt:
      "Summarize Andriy's technical skills based on his notes: languages, backend topics, databases and anything else he has studied. Group them by area and include short code examples from the notes where they exist.",
  },
]
export const CALL_COMMAND: Command = { name: '/call', hint: 'Book a call with Andriy', prompt: CALL_PROMPT }

// Questions for the falling tabs on both sides of the screen; they follow the topics of the notes
export const SIDE_QUESTIONS = [
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
