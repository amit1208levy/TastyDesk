import type { ReactNode } from 'react'

/* A small markdown renderer.

   Deliberately not dangerouslySetInnerHTML: the brief is written to disk by a
   scheduled agent, and rendering a file as raw HTML would make a file write an
   arbitrary script execution. Everything here goes through React as text. */

function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = []
  // Bold, italic, and inline code — the only spans a brief needs.
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = pattern.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const token = m[0]
    const key = `${keyPrefix}-${i++}`
    if (token.startsWith('**')) {
      out.push(
        <strong key={key} className="font-semibold text-ink">
          {token.slice(2, -2)}
        </strong>,
      )
    } else if (token.startsWith('`')) {
      out.push(
        <code key={key} className="mono rounded-sm bg-sunken px-1 text-[0.9em]">
          {token.slice(1, -1)}
        </code>,
      )
    } else {
      out.push(
        <em key={key} className="italic">
          {token.slice(1, -1)}
        </em>,
      )
    }
    last = m.index + token.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function Markdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0

  const flushList = (ordered: boolean) => {
    const items: string[] = []
    const marker = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/
    while (i < lines.length) {
      const m = lines[i].match(marker)
      if (!m) break
      items.push(m[1])
      i++
    }
    const Tag = ordered ? 'ol' : 'ul'
    blocks.push(
      <Tag
        key={`l${blocks.length}`}
        className={`my-2 space-y-1 pl-5 text-sm ${ordered ? 'list-decimal' : 'list-disc'} marker:text-faint`}
      >
        {items.map((it, n) => (
          <li key={n}>{inline(it, `li${blocks.length}-${n}`)}</li>
        ))}
      </Tag>,
    )
  }

  while (i < lines.length) {
    const line = lines[i]

    if (!line.trim()) {
      i++
      continue
    }

    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
      blocks.push(<hr key={`h${blocks.length}`} className="my-4 border-line" />)
      i++
      continue
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/)
    if (heading) {
      const level = heading[1].length
      const text = inline(heading[2], `h${blocks.length}`)
      const cls =
        level === 1
          ? 'mt-1 mb-2 text-base font-semibold tracking-tight'
          : level === 2
            ? 'mt-4 mb-1.5 text-[13px] font-semibold uppercase tracking-wider text-faint'
            : 'mt-3 mb-1 text-sm font-semibold'
      blocks.push(
        <div key={`h${blocks.length}`} className={cls}>
          {text}
        </div>,
      )
      i++
      continue
    }

    if (/^\s*>\s?/.test(line)) {
      const quoted: string[] = []
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quoted.push(lines[i].replace(/^\s*>\s?/, ''))
        i++
      }
      blocks.push(
        <blockquote
          key={`q${blocks.length}`}
          className="my-2 border-l-2 border-accent/50 pl-3 text-sm text-muted"
        >
          {inline(quoted.join(' '), `q${blocks.length}`)}
        </blockquote>,
      )
      continue
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      flushList(false)
      continue
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      flushList(true)
      continue
    }

    // A pipe table: header, separator, rows.
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|/.test(lines[i + 1])) {
      const cells = (row: string) =>
        row
          .replace(/^\s*\|/, '')
          .replace(/\|\s*$/, '')
          .split('|')
          .map((c) => c.trim())
      const head = cells(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].includes('|')) {
        rows.push(cells(lines[i]))
        i++
      }
      blocks.push(
        <div key={`t${blocks.length}`} className="my-3 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-line text-left text-[10px] uppercase tracking-wider text-faint">
                {head.map((h, n) => (
                  <th key={n} className="py-1.5 pr-3 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="num">
              {rows.map((r, n) => (
                <tr key={n} className="border-b border-line/60 last:border-0">
                  {r.map((c, k) => (
                    <td key={k} className="py-1.5 pr-3">
                      {inline(c, `td${n}-${k}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    const para: string[] = []
    while (i < lines.length && lines[i].trim() && !/^\s*(#{1,4}\s|[-*+]\s|\d+[.)]\s|>)/.test(lines[i])) {
      para.push(lines[i])
      i++
    }
    blocks.push(
      <p key={`p${blocks.length}`} className="my-2 text-sm leading-relaxed">
        {inline(para.join(' '), `p${blocks.length}`)}
      </p>,
    )
  }

  return <div className="max-w-none">{blocks}</div>
}
