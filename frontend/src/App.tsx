import { useMemo, useRef, useState } from 'react'

import { ChatMessage } from './components/ChatMessage'
import { Composer } from './components/Composer'
import { FallingQuestions } from './components/FallingQuestions'
import { Hero } from './components/Hero'
import { BinaryPortrait } from './components/BinaryPortrait'
import { StepsLive } from './components/StepsLive'
import { UsageMeter } from './components/UsageMeter'
import { CALL_CARD, CALL_COMMAND, COMMANDS, MODEL_LABEL, QUICK_PROMPTS, SIDE_QUESTIONS } from './config'
import { useChat } from './hooks/useChat'
import { useStickToBottom } from './hooks/useStickToBottom'
import type { Command } from './types'

export default function App() {
  const chat = useChat()
  const { messages, usage, isLoading, streaming, bookingEnabled } = chat
  const [question, setQuestion] = useState('')
  const [home, setHome] = useState(messages.length === 0) // true = the main screen (the portrait)
  const showHero = home || messages.length === 0

  const scrollRef = useRef<HTMLElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const { atBottom, scrollToBottom, scrollToTop } = useStickToBottom(scrollRef, contentRef)

  // the cards of the hero screen, the chips of a started chat and the commands: "call" only if booking is enabled
  const cardPrompts = useMemo(() => (bookingEnabled ? [...QUICK_PROMPTS, CALL_CARD] : QUICK_PROMPTS), [bookingEnabled])
  const commands = useMemo(() => (bookingEnabled ? [...COMMANDS, CALL_COMMAND] : COMMANDS), [bookingEnabled])

  // why the visitor can not ask right now
  const outOfQuestions = usage.questions_used >= usage.questions_limit
  const blockMessage = outOfQuestions ? `QUESTION LIMIT REACHED: ALL ${usage.questions_limit} QUESTIONS ARE USED` : chat.serverNotice
  const cantAsk = isLoading || outOfQuestions

  const ask = (text: string, shown?: string) => {
    if (cantAsk || !text.trim()) return
    setQuestion('')
    setHome(false)
    scrollToBottom()
    void chat.send(text, shown)
  }
  const runCommand = (cmd: Command) => ask(cmd.prompt, cmd.name)

  // the main screen; the conversation is kept and continues with the next question
  const goHome = () => {
    if (isLoading) return
    setHome(true)
    setQuestion('')
    scrollToTop()
  }

  return (
    <div className="shell">
      <FallingQuestions questions={SIDE_QUESTIONS} disabled={cantAsk} onPick={ask} />

      <header className="topbar">
        <button className="brand" title="Back to the main screen" aria-label="Back to the main screen" disabled={isLoading} onClick={goHome}>
          <span className="brand-mark" />
          <span>
            ANDRIY <span className="dim">v2.0</span>
          </span>
        </button>
        <div className="status">
          <span className="stat">
            <i className="dot" />
            LIVE
          </span>
          <span className="stat">
            QUESTIONS{' '}
            <b>
              {usage.questions_used}/{usage.questions_limit}
            </b>
          </span>
          <span className="stat hide-sm">
            MODEL <b>{MODEL_LABEL}</b>
          </span>
          <span className="stat hide-sm">
            THREAD <b>{chat.threadId.slice(0, 6)}</b>
          </span>
        </div>
      </header>

      <div className="body">
        <section className="main">
          <main className="chat-box" ref={scrollRef}>
            <div className="column" ref={contentRef} role={showHero ? undefined : 'log'}>
              {showHero ? (
                <Hero prompts={cardPrompts} disabled={outOfQuestions} onPick={(p) => ask(p.prompt)} />
              ) : (
                <>
                  {/* the same animated portrait, small, in the top left corner of the chat */}
                  <div className="inline-portrait frame">
                    <BinaryPortrait src="/hero.jpg" cellW={3.2} cellH={5} />
                  </div>
                  {messages.map((msg) => (
                    <ChatMessage key={msg.id} msg={msg} onFeedback={chat.sendFeedback} />
                  ))}
                </>
              )}
              {isLoading && !streaming && <StepsLive steps={chat.steps} />}
            </div>
          </main>

          <div className="dock">
            {!showHero && !atBottom && (
              <button type="button" className="jump" onClick={() => scrollToBottom(true)}>
                ↓ LATEST
              </button>
            )}
            {blockMessage && <p className="notice">{blockMessage}</p>}
            <UsageMeter usage={usage} />

            {/* compact chips once the conversation has started */}
            {!showHero && (
              <div className="chips">
                {cardPrompts.map((item) => (
                  <button key={item.label} className="chip" disabled={cantAsk} onClick={() => ask(item.prompt)}>
                    {item.label}
                  </button>
                ))}
              </div>
            )}

            <Composer
              value={question}
              onChange={setQuestion}
              commands={commands}
              placeholder={outOfQuestions ? 'No questions left' : "Ask me about Andriy or use the commands '/'..."}
              disabled={cantAsk}
              running={isLoading}
              onSend={(text) => ask(text)}
              onCommand={runCommand}
              onUnknownCommand={() =>
                chat.setServerNotice(`UNKNOWN COMMAND. TRY ${commands.map((c) => c.name.toUpperCase()).join(', ')}`)
              }
              onStop={chat.stop}
            />
            <p className="fineprint">ANSWERS ARE GENERATED FROM ANDRIY'S NOTES</p>
          </div>
        </section>
      </div>
    </div>
  )
}
