import asyncio

import httpx
import pytest

from polybot.ingestion.transport import FeedResponseTooLarge, make_text_fetch


def test_text_fetch_accepts_a_bounded_same_origin_feed():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/rss+xml; charset=utf-8"},
            content=b"<rss><channel /></rss>",
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetch = make_text_fetch(client=client, max_bytes=1024)
            assert await fetch("https://feeds.example.test/news.xml") == "<rss><channel /></rss>"

    asyncio.run(scenario())


def test_text_fetch_rejects_a_cross_origin_redirect():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            request=request,
            headers={"location": "http://127.0.0.1/private"},
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetch = make_text_fetch(client=client)
            with pytest.raises(httpx.HTTPStatusError):
                await fetch("https://feeds.example.test/news.xml")

    asyncio.run(scenario())


def test_text_fetch_rejects_a_body_over_the_byte_limit():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, content=b"x" * 17)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetch = make_text_fetch(client=client, max_bytes=16)
            with pytest.raises(FeedResponseTooLarge, match="16 bytes"):
                await fetch("https://feeds.example.test/news.xml")

    asyncio.run(scenario())


def test_text_fetch_rejects_an_oversized_declared_length_before_reading():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-length": "1000"},
            content=b"small",
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetch = make_text_fetch(client=client, max_bytes=16)
            with pytest.raises(FeedResponseTooLarge, match="declared length"):
                await fetch("https://feeds.example.test/news.xml")

    asyncio.run(scenario())
