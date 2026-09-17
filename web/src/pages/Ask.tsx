import { useState } from 'react'
import { Markdown } from '../components/Markdown'
import { ErrorPanel, Loading, SectionHeading } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { relativeTime } from '../lib/format'

const EXAMPLES = [
  'What needs a decision today?',
  'Why is my SPY strangle flagged?',
  'Which of my setups actually makes money?',
  'Where am I breaking my own rules?',
]

/* Asking is queued, not answered on the spot.

   This app holds no Anthropic key — there is nothing for it to call — so a
   Claude session picks these up, reads the same fact sheet the brief uses, and
   writes the answer back. That is slower than a chat window and it is said
   plainly here, because an input box that silently never replies would be
   worse than no input box. */
export function Ask() {
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const thread = useAsync(() => api.questionThread(), [], 20_000)

  async function send(text: string) {
    const question = text.trim()
    if (!question) return
    setSending(true)
    setProblem(null)
    try {
      await api.ask(question)
      setDraft('')
      thread.reload()
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e))
    } finally {
      setSending(false)
    }
  }

  const pending = (thread.data ?? []).filter((q) => !q.answer).length

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <SectionHeading
        title="Ask about your book"
        hint="answered by a Claude session reading your live positions"
      />

      <div className="rounded-card border border-line bg-raised p-4">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') void send(draft)
          }}
          rows={3}
          placeholder="What needs a decision today?"
          className="w-full resize-y rounded-sm border border-line bg-bg px-3 py-2 text-sm outline-none placeholder:text-faint focus:border-line-strong"
        />

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            onClick={() => void send(draft)}
            disabled={sending || !draft.trim()}
            className="rounded-sm border border-line-strong px-3 py-1.5 text-xs transition-colors hover:bg-hover disabled:opacity-50"
          >
            {sending ? 'Queueing…' : 'Ask'}
          </button>
          <span className="text-[11px] text-faint">⌘↵ to send</span>
          {pending > 0 && (
            <span className="ml-auto text-[11px] text-muted">
              {pending} waiting for a Claude session
            </span>
          )}
        </div>

        {problem && <div className="mt-2 text-xs text-loss">{problem}</div>}

        <div className="mt-3 border-t border-line pt-2.5">
          <div className="text-[11px] text-faint">
            Answers are not instant. The app holds no API key, so a Claude session picks these up,
            reads your positions, and writes back. That happens at{' '}
            <strong className="font-medium text-muted">16:00, 18:00, 20:00 and 22:00</strong> on
            weekdays — or straight away if you ask Claude in chat to answer what is waiting.
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {EXAMPLES.map((q) => (
              <button
                key={q}
                onClick={() => setDraft(q)}
                className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted transition-colors hover:bg-hover hover:text-ink"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      </div>

      {thread.error ? (
        <ErrorPanel error={thread.error} onRetry={thread.reload} />
      ) : !thread.data ? (
        <Loading />
      ) : thread.data.length === 0 ? null : (
        <div className="space-y-3">
          {thread.data.map((q) => (
            <div key={q.id} className="rounded-card border border-line bg-raised p-4">
              <div className="flex items-baseline gap-2">
                <div className="flex-1 text-sm font-medium">{q.question}</div>
                <span className="shrink-0 text-[10px] text-faint">{relativeTime(q.asked_at)}</span>
              </div>

              {q.answer ? (
                <div className="mt-2 border-t border-line pt-2">
                  <Markdown source={q.answer} />
                  <div className="mt-1 text-[10px] text-faint">
                    answered {relativeTime(q.answered_at)}
                  </div>
                </div>
              ) : (
                <div className="mt-2 flex items-center gap-2 border-t border-line pt-2 text-xs text-faint">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-watch" />
                  Waiting for a Claude session. Ask in chat to have it answered now.
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
