import { useState } from 'react'
import { checkPairing, parsePairingCode, savePairing } from '../lib/connection'

/* The iPhone app's first screen, and where it returns if the Mac stops
   accepting it. One field: the code `tastydesk pair` put on the Mac's
   clipboard, which Universal Clipboard has already carried to this phone. */
export function PhonePairing({ onPaired, refused }: { onPaired: () => void; refused?: boolean }) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(
    refused ? 'Your Mac no longer accepts this phone. Pair it again.' : null,
  )

  async function paste() {
    try {
      setCode(await navigator.clipboard.readText())
      setProblem(null)
    } catch {
      setProblem('Could not read the clipboard. Press and hold the field, then Paste.')
    }
  }

  async function connect() {
    const pairing = parsePairingCode(code)
    if (!pairing) {
      setProblem('That is not a pairing code. It starts with https:// and has a # in it.')
      return
    }
    setBusy(true)
    setProblem(null)
    const result = await checkPairing(pairing)
    if (result === 'ok') {
      await savePairing(pairing)
      setCode('')
      onPaired()
    } else if (result === 'refused') {
      setProblem('Your Mac did not accept that code. Run the pairing again for a fresh one.')
    } else {
      setProblem('Could not reach your Mac. Is Tailscale on here, and Tasty Desk running there?')
    }
    setBusy(false)
  }

  return (
    <div className="safe-area min-h-full">
      <div className="mx-auto flex min-h-full w-full max-w-md flex-col justify-center px-5 py-12">
        <div className="display gold-text mb-1 text-[28px]">Tasty Desk</div>
        <p className="mb-6 text-[15px] text-muted">
          Connect to the Tasty Desk running on your Mac. Your account data stays there; this phone
          only reads it, over your private Tailscale network.
        </p>

        <ol className="mb-6 list-decimal space-y-1.5 pl-5 text-[14px] text-muted">
          <li>Turn Tailscale on, on this phone.</li>
          <li>
            On the Mac, run <code className="mono rounded-sm bg-sunken px-1">./scripts/setup-phone.sh</code>.
            It copies a pairing code.
          </li>
          <li>Paste it below.</li>
        </ol>

        <label className="label mb-1.5 block" htmlFor="pairing-code">
          Pairing code
        </label>
        <textarea
          id="pairing-code"
          value={code}
          onChange={(e) => {
            setCode(e.target.value)
            setProblem(null)
          }}
          rows={3}
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          className="mono w-full rounded-card border border-line-strong bg-raised px-3 py-2 text-[14px] break-all"
          placeholder="https://your-mac.tailnet.ts.net#…"
        />

        {problem && (
          <div className="mt-2.5 rounded-card border border-danger/40 bg-danger-soft px-3 py-2 text-[14px] text-danger">
            {problem}
          </div>
        )}

        <div className="mt-4 flex gap-2">
          <button
            onClick={paste}
            disabled={busy}
            className="rounded-full border border-line-strong px-4 py-2.5 text-[15px] hover:bg-hover disabled:opacity-50"
          >
            Paste
          </button>
          <button
            onClick={connect}
            disabled={busy || !code.trim()}
            className="flex-1 rounded-full bg-accent px-4 py-2.5 text-[15px] font-medium text-white disabled:opacity-50"
          >
            {busy ? 'Connecting…' : 'Connect'}
          </button>
        </div>
      </div>
    </div>
  )
}
