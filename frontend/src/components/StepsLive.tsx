const SEGMENTS = 8

/** what the agent is doing right now, before the first token of the answer arrives */
export function StepsLive({ steps }: { steps: string[] }) {
  return (
    <div className="steps-live frame" role="status">
      <p className="steps-head">
        <span>[AGENT] SYSTEM STATUS</span>
        <span>{steps.length} STEPS</span>
      </p>
      <div className="seg">
        {Array.from({ length: SEGMENTS }, (_, n) => (
          <i key={n} className={n < steps.length ? 'on' : undefined} />
        ))}
      </div>
      <ul>
        {steps.map((s, i) => (
          <li key={i} className={i === steps.length - 1 ? 'active' : undefined}>
            <span className="step-icon" />
            {s}
          </li>
        ))}
      </ul>
      {steps.length === 0 && <div className="loading-indicator">CONNECTING...</div>}
    </div>
  )
}
