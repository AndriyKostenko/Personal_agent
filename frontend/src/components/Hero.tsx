import type { Prompt } from '../types'
import { BinaryPortrait } from './BinaryPortrait'

interface Props {
  prompts: Prompt[]
  disabled: boolean
  onPick: (prompt: Prompt) => void
}

/** the main screen: the animated binary portrait and the ready-made questions */
export function Hero({ prompts, disabled, onPick }: Props) {
  return (
    <section className="hero">
      <div className="portrait frame">
        <span className="cap cap-tl">[01] SUBJECT</span>
        <span className="cap cap-tr">ANDRIY.IMG</span>
        <BinaryPortrait src="/hero.jpg" />
        <span className="cap cap-bl">{'BINARY // DECODE'}</span>
        <span className="cap cap-br">CLICK TO REPLAY</span>
      </div>
      <h1>
        Andriy <span className="ver">v2.0</span>
      </h1>
      <p className="role">SOFTWARE ENGINEER</p>
      <p className="lead">I'm AgentAndriy, Andriy's personal agent.</p>
      <div className={prompts.length > 3 ? 'cards cards--four' : 'cards'}>
        {prompts.map((item) => (
          <button key={item.label} className="card frame" disabled={disabled} onClick={() => onPick(item)}>
            <span className="arrow">↗</span>
            <span className="tag">{item.tag}</span>
            <strong>{item.label}</strong>
            <small>{item.hint}</small>
          </button>
        ))}
      </div>
      <p className="legal">
        <a href="/privacy">Privacy Policy</a>
      </p>
    </section>
  )
}
