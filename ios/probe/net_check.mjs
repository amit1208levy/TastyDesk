// Checks that the tastytrade library's HTTP client reaches tastytrade's REST
// API from Pyodide (it switches to the browser's fetch there). No credentials
// are sent, so 401 and 400 are the expected, healthy answers.
import { loadPyodide } from "pyodide";

const py = await loadPyodide();
await py.loadPackage("micropip");
await py.pyimport("micropip").install(["tastytrade"]);
console.log(await py.runPythonAsync(`
import sys
import httpx2

client = httpx2.AsyncClient(base_url="https://api.tastyworks.com")
quotes = await client.get("/market-data/by-type", params={"equity": ["SPY"]})
token = await client.post("/oauth/token", json={"grant_type": "refresh_token"})
"\\n".join([
    f"platform: {sys.platform}, transport: {type(client._transport).__name__}",
    f"REST quotes GET: HTTP {quotes.status_code} (401 expected without a token)",
    f"OAuth token POST: HTTP {token.status_code} (400 expected without a token)",
])
`));
