import asyncio
import httpx
from fastapi import FastAPI
from backend.app import naive_routes


def test_built_ui_can_read_feed_and_send_batch_controls(monkeypatch):
    original_client = httpx.AsyncClient
    seen = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"ok":true}'

    async def upstream(request):
        seen.append(request)
        return httpx.Response(200, stream=Body(), headers={"content-type": "application/json"})

    monkeypatch.setattr(naive_routes.httpx, "AsyncClient", lambda **kw: original_client(
        transport=httpx.MockTransport(upstream), **kw))
    app = FastAPI()
    app.include_router(naive_routes.router)

    async def check():
        async with original_client(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get("/naive-api/pipeline")
            assert result.status_code == 200
            result = await client.post("/naive-api/pipeline/cancel?batch_id=batch",
                                       headers={"X-PlayLattice-Control": "1"})
            assert result.status_code == 200
        assert str(seen[0].url) == "http://127.0.0.1:8011/pipeline"
        assert seen[1].headers["x-playlattice-control"] == "1"
        assert seen[1].url.query == b"batch_id=batch"
    asyncio.run(check())


def test_offline_integration_returns_service_unavailable(monkeypatch):
    original_client = httpx.AsyncClient

    async def offline(request):
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(naive_routes.httpx, "AsyncClient", lambda **kw: original_client(
        transport=httpx.MockTransport(offline), **kw))
    app = FastAPI()
    app.include_router(naive_routes.router)

    async def check():
        async with original_client(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get("/naive-api/pipeline")
            assert result.status_code == 503
    asyncio.run(check())
