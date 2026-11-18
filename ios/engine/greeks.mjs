// The greeks check: the phone reads greeks from tastytrade's live stream the
// way the Mac does.
//
// Greeks come only from tastytrade's live stream (a websocket). The phone's
// HTTP client cannot hold one open, so the phone opens it with the page's own
// WebSocket (tastydesk_phone._install_live_greeks) under the same stream
// library. A stand-in stream server here speaks the stream's protocol; the
// phone engine (Pyodide, the browser-style WebSocket) and the Mac's Python
// (its own websocket client) both ask it for the same options through
// MarkService, and must read the same greeks. Then a stream that refuses the
// connection: the phone gives up at once, with the reason, and greeks stay
// unknown rather than made up.
//
// Run after npm run bundle, from ios/: npm run greeks
import { loadPyodide } from "../../web/public/engine/pyodide.js";
import createPyodideModule from "../../web/public/engine/pyodide.asm.js";
import { bootEngine } from "../../web/src/phone/boot.ts";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { WebSocketServer } from "ws";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const failures = [];
const expect = (ok, what) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failures.push(what);
};

// The stand-in stream: setup, auth, a channel, the feed, then one Greeks
// event per symbol in the compact format, in the field order the client asked for.
const GREEKS = {
  ".SPY270115P600": { price: 12.5, volatility: 0.231, delta: -0.284, gamma: 0.0041, theta: -0.0712, rho: -0.33, vega: 0.981 },
  "./ESZ26P6500:XCME": { price: 41.25, volatility: 0.187, delta: -0.212, gamma: 0.0009, theta: -1.284, rho: -0.71, vega: 6.402 },
};
const server = new WebSocketServer({ host: "127.0.0.1", port: 0 });
await new Promise((resolve) => server.on("listening", resolve));
const url = `ws://127.0.0.1:${server.address().port}/realtime`;
let tokensSeen = [];
server.on("connection", (socket) => {
  let fields = [];
  const send = (m) => socket.send(JSON.stringify(m));
  socket.on("message", (raw) => {
    const m = JSON.parse(String(raw));
    if (m.type === "SETUP") {
      send({ type: "SETUP", channel: 0, keepaliveTimeout: 60, acceptKeepaliveTimeout: 60, version: "stand-in" });
      send({ type: "AUTH_STATE", channel: 0, state: "UNAUTHORIZED" });
    } else if (m.type === "AUTH") {
      tokensSeen.push(m.token);
      send({ type: "AUTH_STATE", channel: 0, state: "AUTHORIZED", userId: "x" });
    } else if (m.type === "CHANNEL_REQUEST") {
      send({ type: "CHANNEL_OPENED", channel: m.channel, service: "FEED", parameters: m.parameters });
    } else if (m.type === "FEED_SETUP") {
      fields = m.acceptEventFields.Greeks ?? [];
      send({ type: "FEED_CONFIG", channel: m.channel, dataFormat: "COMPACT", aggregationPeriod: 0.1 });
    } else if (m.type === "FEED_SUBSCRIPTION") {
      const values = [];
      for (const { symbol } of m.add ?? []) {
        const g = GREEKS[symbol];
        if (!g) continue;
        for (const f of fields) {
          if (f === "eventType") values.push("Greeks");
          else if (f === "eventSymbol") values.push(symbol);
          else values.push(f in g ? g[f] : 0);
        }
      }
      send({ type: "FEED_DATA", channel: m.channel, data: ["Greeks", values] });
    }
  });
});

// Both sides ask through MarkService._collect_greeks, as a refresh does.
const ask = (wssUrl) => `
import asyncio, json
from tastydesk.core import marks

class Session:
    is_test = False
    client_kwargs = {}
    async def _get(self, path):
        assert path == "/api-quote-tokens"
        return {"dxlink-url": ${JSON.stringify(wssUrl)}, "token": "stand-in-token"}
    async def refresh(self):
        pass

class Client:
    session = Session()

async def main():
    service = marks.MarkService(Client())
    events = await service._collect_greeks(${JSON.stringify(Object.keys(GREEKS))}, 8)
    keys = ("delta", "gamma", "theta", "vega", "volatility")
    return {s: {k: str(getattr(e, k)) for k in keys} for s, e in sorted(events.items())}
`;

console.log("1/3 The Mac's Python reads the stand-in stream...");
// Not execFileSync: the stand-in answers from this same process, so it has to
// keep running while the Mac's Python talks to it.
const { stdout } = await promisify(execFile)(
  "uv",
  ["run", "--quiet", "python", "-c", `${ask(url)}\nprint(json.dumps(asyncio.run(main())))`],
  // The stand-in is on this machine: not through any proxy the shell has set.
  { cwd: repo, env: { ...process.env, NO_PROXY: "127.0.0.1,localhost", no_proxy: "127.0.0.1,localhost" } },
);
const mac = JSON.parse(stdout.trim().split("\n").pop());
expect(Object.keys(mac).length === 2, `the Mac read greeks for ${Object.keys(mac).length} of 2 options`);

console.log("2/3 The phone engine reads it through the page's WebSocket...");
const engine = await bootEngine({
  indexURL: path.resolve(repo, "web/public/engine") + "/",
  loadPyodide,
  createPyodideModule,
  readFile: async (file) => new Uint8Array(readFileSync(file)),
  stdout: () => {},
  stderr: () => {},
});
const py = engine.pyodide;
await py.runPythonAsync("import tastydesk_phone; tastydesk_phone._install_live_greeks()");
const phone = JSON.parse(await py.runPythonAsync(`${ask(url)}\njson.dumps(await main())`));
expect(Object.keys(phone).length === 2, `the phone read greeks for ${Object.keys(phone).length} of 2 options`);
expect(JSON.stringify(phone) === JSON.stringify(mac), "the phone's greeks are the Mac's, to the digit");
expect(phone[".SPY270115P600"]?.delta === "-0.284", `SPY put delta ${phone[".SPY270115P600"]?.delta}`);
expect(tokensSeen.length === 2 && tokensSeen.every((t) => t === "stand-in-token"), "both signed in with the stream token");
const live = engine.status().live_greeks;
expect(live?.ok === true, `the setup page says the stream connected (${JSON.stringify(live)})`);

console.log("3/3 A stream that refuses the connection...");
server.close();
const started = Date.now();
const none = JSON.parse(await py.runPythonAsync(`${ask(url)}\njson.dumps(await main())`));
const seconds = (Date.now() - started) / 1000;
expect(Object.keys(none).length === 0, "no greeks are made up");
expect(seconds < 5, `the phone gives up at once (${seconds.toFixed(1)} s)`);
const refused = engine.status().live_greeks;
expect(refused?.ok === false && !!refused.reason, `and says why: ${refused?.reason}`);
expect(!JSON.stringify(refused).includes("stand-in-token"), "without the token");

if (failures.length) {
  console.log(`\n${failures.length} greeks check(s) failed.`);
  process.exit(1);
}
console.log("\nThe phone reads live greeks the way the Mac does.");
process.exit(0);
