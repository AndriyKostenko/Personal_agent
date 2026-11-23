import { useMemo, useState, type KeyboardEvent } from 'react'

import type { Command } from '../types'

interface Props {
  value: string
  onChange: (value: string) => void
  commands: Command[]
  placeholder: string
  /** the field is disabled (a request is running or no questions are left) */
  disabled: boolean
  /** a request is running: the send button becomes a Stop button */
  running: boolean
  onSend: (text: string) => void
  onCommand: (command: Command) => void
  onUnknownCommand: () => void
  onStop: () => void
}

/** the prompt field with the slash-command menu ("/" opens it) and the send / stop button */
export function Composer({ value, onChange, commands, placeholder, disabled, running, onSend, onCommand, onUnknownCommand, onStop }: Props) {
  const [active, setActive] = useState(0)

  // the menu while the visitor is typing the command name ("/" or "/sk")
  const suggestions = useMemo(() => {
    const typed = value.trimStart().toLowerCase()
    if (!typed.startsWith('/') || typed.includes(' ')) return []
    return commands.filter((c) => c.name.startsWith(typed))
  }, [value, commands])
  const menuOpen = suggestions.length > 0 && !disabled
  const activeIndex = Math.min(active, Math.max(0, suggestions.length - 1))

  const change = (text: string) => {
    setActive(0)
    onChange(text)
  }

  const submit = () => {
    const typed = value.trim()
    if (!typed.startsWith('/')) return onSend(typed)
    const cmd = commands.find((c) => c.name === typed.toLowerCase()) ?? (suggestions.length === 1 ? suggestions[0] : undefined)
    if (cmd) onCommand(cmd)
    else onUnknownCommand()
  }

  // keyboard selection in the menu: arrows move, Enter runs, Tab completes, Esc closes
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!menuOpen || e.nativeEvent.isComposing) return
    const count = suggestions.length
    const current = suggestions[activeIndex]
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((activeIndex + 1) % count)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((activeIndex - 1 + count) % count)
    } else if (e.key === 'Enter' && current) {
      e.preventDefault() // otherwise the form would submit the half-typed command
      onCommand(current)
    } else if (e.key === 'Tab' && current) {
      e.preventDefault()
      change(current.name)
    } else if (e.key === 'Escape') {
      change('')
    }
  }

  return (
    <div className="composer-wrap">
      {menuOpen && (
        <div id="slash-menu" className="slash frame" role="listbox">
          {suggestions.map((c, i) => (
            <button
              key={c.name}
              id={`slash-${i}`}
              type="button"
              className={i === activeIndex ? 'slash-item active' : 'slash-item'}
              role="option"
              aria-selected={i === activeIndex}
              onMouseMove={() => setActive(i)}
              onClick={() => onCommand(c)}
            >
              <b>{c.name}</b>
              <span>{c.hint}</span>
            </button>
          ))}
        </div>
      )}
      <form
        className="composer frame"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <input
          value={value}
          onChange={(e) => change(e.target.value)}
          type="text"
          placeholder={placeholder}
          maxLength={1000}
          disabled={disabled}
          aria-label="Your question"
          aria-controls={menuOpen ? 'slash-menu' : undefined}
          aria-activedescendant={menuOpen ? `slash-${activeIndex}` : undefined}
          onKeyDown={onKeyDown}
        />
        {running ? (
          // Stop closes the connection; the server then cancels the LLM call (no more tokens are paid for)
          <button type="button" className="send stop" title="Stop" aria-label="Stop the answer" onClick={onStop}>
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
              <rect x="5" y="5" width="14" height="14" />
            </svg>
          </button>
        ) : (
          <button type="submit" className="send" title="Send" aria-label="Send" disabled={disabled || !value.trim()}>
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
          </button>
        )}
      </form>
    </div>
  )
}
