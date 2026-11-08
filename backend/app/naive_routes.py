"""Same-origin access to the loopback Naive integration for the built frontend."""
import httpx
from fastapi import APIRouter, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, StreamingResponse

router = APIRouter()
HOP_HEADERS = {"host", "connection", "transfer-encoding", "content-length", "accept-encoding"}


@router.api_route("/naive-api/{path:path}", methods=["GET", "HEAD", "POST"])
async def naive_proxy(path: str, request: Request):
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=3), follow_redirects=False)
    url = httpx.URL("http://127.0.0.1:8011/" + path, query=request.url.query.encode() if request.url.query else None)
    headers = {
        key: value for key, value in request.headers.items()
        if key.lower() not in HOP_HEADERS | {"cookie", "authorization"}
    }
    try:
        upstream = await client.send(
            client.build_request(request.method, url, headers=headers, content=request.stream()),
            stream=True,
        )
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse(
            {"detail": "Naive integration is unavailable on the host Mac (port 8011)."},
            status_code=503,
        )

    async def close():
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_raw(), status_code=upstream.status_code,
        headers={key: value for key, value in upstream.headers.items()
                 if key.lower() not in {"connection", "transfer-encoding", "content-length"}},
        background=BackgroundTask(close),
    )
