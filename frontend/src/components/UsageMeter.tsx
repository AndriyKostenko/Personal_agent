import type { Usage } from '../types'

/** how many questions were asked, as a segmented bar above the prompt field */
export function UsageMeter({ usage }: { usage: Usage }) {
  const left = usage.questions_limit - usage.questions_used
  return (
    <div className={left <= 1 ? 'usage low' : 'usage'}>
      <span>QUESTIONS</span>
      <span className="usage-seg" role="img" aria-label={`${usage.questions_used} of ${usage.questions_limit} questions used`}>
        {Array.from({ length: usage.questions_limit }, (_, n) => (
          <i key={n} className={n < usage.questions_used ? 'on' : undefined} />
        ))}
      </span>
      <b>
        {usage.questions_used}/{usage.questions_limit}
      </b>
    </div>
  )
}
