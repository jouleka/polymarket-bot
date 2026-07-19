"""Real network adapters for the ingestion layer (httpx + websockets).

Thin glue only: a Data API ``fetch`` for DataApiPoller and a WS connection for
MarketSocket — both satisfy the interfaces the tested core already expects, so
the core stays network-free and these stay logic-free.
"""

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"
CLOB_MARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
# Free, keyless Polygon JSON-RPC (verified reachable 2026-06-25; publicnode / drpc /
# onfinality also work). polygon-rpc.com and ankr now gate behind an API key.
POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"

# The reconnect_on tuple a MarketSocket MUST use with the real ws transport: a
# normal disconnect raises websockets.ConnectionClosed, which is NOT an OSError,
# so OSError alone would let the disconnect escape run() (no reconnect / resync).
WS_RECONNECT_ON = (OSError, ConnectionClosed)


def make_httpx_fetch(base_url=DATA_API_URL, timeout=15.0, client=None):
    """Return an async fetch(path, params) -> parsed JSON for DataApiPoller.

    Pass a shared httpx.AsyncClient for the continuous poll loop; otherwise a
    short-lived client is created per call (fine for one-off polls).
    """
    owned = client is None

    async def fetch(path, params):
        c = client or httpx.AsyncClient(base_url=base_url, timeout=timeout)
        try:
            resp = await c.get(path, params=params)
            resp.raise_for_status()
            return resp.json()
        finally:
            if owned:
                await c.aclose()

    return fetch


def make_text_fetch(timeout=15.0, client=None):
    """Return an async ``fetch(url) -> text`` for the news poller (GET a feed body).
    Follows redirects and sets a UA; raises on HTTP error (fail loud)."""
    owned = client is None

    async def fetch(url):
        c = client or httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                        headers={"user-agent": "polybot/0.1"})
        try:
            resp = await c.get(url)
            resp.raise_for_status()
            return resp.text
        finally:
            if owned:
                await c.aclose()

    return fetch


async def open_market_ws(url=CLOB_MARKET_WS):
    """Connect to the public CLOB market channel; satisfies MarketSocket's
    transport interface (async-iterable of text frames + async send()).

    ping_interval=None disables the library's protocol ping; the venue's
    app-level keepalive (client sends "PING", server replies "PONG" — driven by
    MarketSocket's keepalive task) governs liveness instead.
    """
    # One independent queue per shard otherwise multiplies websockets' default
    # high-water mark across the production fan-out. A single queued frame keeps
    # memory bounded; TCP backpressure plus the existing top-of-book gap detector
    # preserve fail-closed resync semantics if the event loop falls behind.
    return await websockets.connect(url, ping_interval=None, max_queue=1)


def is_transient_rpc_error(exc):
    """Classify an exception from ``make_rpc_fetch`` for ``retry.with_retry``: True for a
    TRANSIENT failure worth retrying -- a network/timeout error, or a 429/5xx HTTP status
    from the free public RPC. A 4xx (other than 429) or a JSON-RPC application error
    (the RuntimeError ``make_rpc_fetch`` raises on ``body['error']``) is NOT transient --
    it is a contract/format problem and must fail loud. httpx-specific glue, hence here."""
    if isinstance(exc, httpx.TransportError):  # covers timeouts + network/protocol errors
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or 500 <= code < 600
    return False


def make_rpc_fetch(rpc_url=POLYGON_RPC, timeout=30.0, client=None):
    """Return an async ``fetch(method, params) -> result`` (JSON-RPC) for the Polygon
    log watcher. Raises on a JSON-RPC error or HTTP error (fail loud). Pass a shared
    httpx.AsyncClient for the continuous watch loop; otherwise one is made per call.
    """
    owned = client is None

    async def fetch(method, params):
        c = client or httpx.AsyncClient(timeout=timeout)
        try:
            # Some free RPCs (publicnode) 403 the default python-httpx UA on eth_getLogs.
            resp = await c.post(rpc_url, headers={"user-agent": "polybot/0.1"},
                                json={"jsonrpc": "2.0", "id": 1,
                                      "method": method, "params": params})
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                raise RuntimeError(f"Polygon RPC error for {method}: {body['error']}")
            return body["result"]
        finally:
            if owned:
                await c.aclose()

    return fetch
