/* What Amit sees the very first time he opens this. Not being set up yet is a
   starting point, not a failure, so it does not wear the red error styling —
   and the one thing that must land is that the secrets go into a terminal
   prompt, never into a chat window. */
export function Onboarding({ detail }: { detail?: string }) {
  return (
    <div className="mx-auto max-w-2xl">
      <div className="rounded-card border border-line bg-raised p-6">
        <h2 className="text-[18px] font-semibold">Connect your tastytrade account</h2>
        <p className="mt-1 text-[16px] text-muted">
          One-time setup. Everything stays on this Mac.
        </p>

        <ol className="mt-5 space-y-4">
          <li className="flex gap-3">
            <span className="num mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-sunken text-[13px] font-medium text-muted">
              1
            </span>
            <div className="min-w-0">
              <div className="text-[16px] font-medium">Create a read-only API key</div>
              <p className="mt-0.5 text-[14px] text-muted">
                At my.tastytrade.com → Manage → My Profile → API → OAuth Applications. Give it the{' '}
                <strong className="font-medium text-ink">read</strong> scope and nothing else, then{' '}
                <em>Manage → Create Grant</em> to get a refresh token.
              </p>
              <p className="mt-1 text-[14px] text-faint">
                With only the read scope, this app cannot place, change or cancel an order — even if it
                tried.
              </p>
            </div>
          </li>

          <li className="flex gap-3">
            <span className="num mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-sunken text-[13px] font-medium text-muted">
              2
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[16px] font-medium">Store it in the macOS Keychain</div>
              <p className="mt-0.5 text-[14px] text-muted">
                There is no form for this on purpose. Anything typed into a web page travels through
                the browser and can end up in a log; the Keychain prompt cannot.
              </p>
              <p className="mt-1.5 text-[14px] text-muted">
                Open the <strong className="font-medium text-ink">Terminal tab</strong> beside your
                Claude conversation — or Terminal.app (⌘-Space, type "Terminal") — and paste:
              </p>
              <pre className="mono mt-1.5 overflow-x-auto rounded-sm border border-line bg-sunken px-2.5 py-3.5 text-[14px]">
                cd ~/Desktop/DashboardV3 && ./scripts/setup-credentials.sh
              </pre>
              <p className="mt-1.5 text-[14px] text-muted">
                It asks for the two values one at a time.{' '}
                <strong className="font-medium text-ink">Nothing appears as you type</strong> — no dots,
                no stars. That is deliberate. Paste and press return.
              </p>
              <p className="mt-1.5 text-[14px] text-tested">
                Do not paste the client secret or refresh token into a chat window — a transcript keeps
                them forever.
              </p>
            </div>
          </li>

          <li className="flex gap-3">
            <span className="num mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-sunken text-[13px] font-medium text-muted">
              3
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[16px] font-medium">Restart and sync</div>
              <pre className="mono mt-1.5 overflow-x-auto rounded-sm border border-line bg-sunken px-2.5 py-3.5 text-[14px]">
                cd ~/Desktop/DashboardV3 && ./run.sh
              </pre>
              <p className="mt-1 text-[14px] text-muted">
                Then reload this page, open the Health tab and press <em>Sync new activity</em>. The
                first sync pulls two years of history and may take a minute.
              </p>
              <p className="mt-1 text-[14px] text-faint">
                Still says not connected? Run <code className="mono">uv run tastydesk doctor</code> — it
                names which of the two things is wrong.
              </p>
            </div>
          </li>
        </ol>

        {detail && (
          <details className="mt-5 border-t border-line pt-3">
            <summary className="cursor-pointer text-[14px] text-faint">What the server reported</summary>
            <pre className="mono mt-2 whitespace-pre-wrap text-[13px] text-muted">{detail}</pre>
          </details>
        )}
      </div>
    </div>
  )
}
