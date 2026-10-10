# Tasty Desk on the iPhone

The iPhone app is the same dashboard as on the Mac, packaged with
[Capacitor](https://capacitorjs.com) and delivered through TestFlight. It holds
no tastytrade credentials and does no maths of its own: it reads the Tasty Desk
running on your Mac, over your private [Tailscale](https://tailscale.com)
network.

```
iPhone app ──HTTPS, tailnet only──▶ tailscale serve ──▶ 127.0.0.1:8788 ──▶ the same engine
 (pairing token in iOS Keychain)     (on the Mac)        (token required)    as 127.0.0.1:8787
```

Two locks have to open before the phone gets an answer:

1. **Tailscale.** Only devices signed in to your tailnet can reach the Mac's
   `*.ts.net` address. It is not on the internet (this uses `serve`, never `funnel`).
2. **The pairing token.** Every request carries it; the Mac compares it in
   constant time with a SHA-256 hash in its Keychain. The token itself is
   stored only on the phone, in the iOS Keychain, set to *this device only*, so
   it is neither synced to iCloud nor restored onto another phone.

The phone listener serves `/api` and nothing else, accepts browser requests only
from the app's own origin (`capacitor://localhost`), and is opened only while a
phone is paired. The dashboard's own listener now answers only requests
addressed to `127.0.0.1` or `localhost`, so pointing the tunnel at the wrong
port gets you a refusal rather than the account.

## What you need

- An [Apple Developer Program](https://developer.apple.com/programs/) membership
  (paid yearly). TestFlight is not available without it.
- Xcode on the Mac, kept up to date — App Store Connect only accepts builds made
  with a recent SDK.
- Tailscale on the Mac and the iPhone, signed in to the same account.

## One time: App Store Connect

1. **developer.apple.com → Membership** — note your **Team ID** (10 characters).
2. **Xcode → Settings → Accounts** — add the Apple ID of that team.
3. **appstoreconnect.apple.com → Apps → + → New App**: platform iOS, name
   *Tasty Desk* (or anything free), bundle ID **`com.tastydesk.app`**, SKU
   anything. If that bundle ID is taken, change it in `web/capacitor.config.ts`
   *and* in Xcode (target *App* → Signing & Capabilities), then register yours.
   You may need to run `scripts/testflight.sh` once first so Xcode registers
   the bundle ID, then create the app record and run it again.
4. **TestFlight → Internal Testing → +** — make a group and add yourself.

## Each build: upload to TestFlight

```bash
APPLE_TEAM_ID=ABCDE12345 ./scripts/testflight.sh
```

It builds the pages, syncs them into the Xcode project, archives a release
build numbered by the current time, and uploads it. Builds are marked
**internal testing only**: they can go to people on your team, and cannot be
sent for external beta review by mistake.

After Apple processes the build (usually 5–30 minutes), install the
**TestFlight** app on the iPhone and the build appears there.

Prefer Xcode? `cd web && npm run build && npx cap sync ios && npx cap open ios`,
pick your team under *Signing & Capabilities*, then **Product → Archive →
Distribute App → TestFlight Internal Only**.

## One time: pair the phone with the Mac

On the Mac, in the Tailscale admin console
([DNS settings](https://login.tailscale.com/admin/dns)) turn on **MagicDNS**
and **HTTPS Certificates**. Then:

```bash
./scripts/setup-phone.sh
```

It sets up the tunnel and copies a pairing code to the clipboard (it is never
printed). Restart Tasty Desk on the Mac so it opens the phone listener. On the
iPhone, with Tailscale on, open Tasty Desk, tap **Paste**, then **Connect** —
Universal Clipboard carries the code across when both devices use the same
Apple ID. If it does not, run `uv run tastydesk pair --url https://<your-mac>.ts.net --show`
and send the code to yourself some private way.

| To | Run, on the Mac |
|---|---|
| replace the phone's pairing | `./scripts/setup-phone.sh` again (the old code stops working after a restart) |
| lock the phone out | `uv run tastydesk unpair`, then restart Tasty Desk |
| remove the tunnel | `tailscale serve reset` |

**Forget this Mac** on the phone's *History & health* tab removes the code from
the phone.

## Limits

- The Mac has to be awake and Tasty Desk running for the phone to show
  anything. The phone keeps no copy of your account.
- The dashboard was built for a wide window. It works on a phone — tabs fold
  into the *More* menu and tables scroll sideways — but some views are dense.
- Only iPhone builds are configured; iPad is off.

## Development

```bash
cd web
npm run build && npx cap sync ios   # copy the latest pages into the Xcode project
npx cap open ios                    # run on a simulator or a cabled iPhone
```

The app icon's source is `icon/AppIcon.svg`; the 1024 px PNG in
`App/App/Assets.xcassets/AppIcon.appiconset/` is rendered from it, with no
alpha channel.
