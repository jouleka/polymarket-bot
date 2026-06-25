"""Real network adapters for the ingestion layer (httpx + websockets).

Thin glue only: a Data API ``fetch`` for DataApiPoller and a WS connection for
MarketSocket — both satisfy the interfaces the tested core already expects, so
the core stays network-free and these stay logic-free.
"""

import httpx
import websockets

GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"
CLOB_MARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


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


async def open_market_ws(url=CLOB_MARKET_WS):
    """Connect to the public CLOB market channel; satisfies MarketSocket's
    transport interface (async-iterable of text frames + async send()).

    ping_interval=None disables the library's protocol ping so the venue's
    app-level keepalive (TODO: dedicated pong responder) governs liveness.
    """
    return await websockets.connect(url, ping_interval=None)
