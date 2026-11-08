from fastapi.testclient import TestClient

from scripts.serve_website import create_app


def test_preview_video_supports_seek_ranges_and_rejects_invalid_ranges(tmp_path):
    media = bytes(range(256)) * 16
    (tmp_path / "demo.mp4").write_bytes(media)
    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/demo.mp4", headers={"Range": "bytes=1024-1279"})
        assert response.status_code == 206
        assert response.content == media[1024:1280]
        assert response.headers["content-range"] == "bytes 1024-1279/4096"
        assert response.headers["accept-ranges"] == "bytes"
        response = client.get("/demo.mp4", headers={"Range": "bytes=-256"})
        assert response.status_code == 206
        assert response.content == media[-256:]
        assert client.get("/demo.mp4", headers={"Range": "bytes=5000-"}).status_code == 416
        assert client.head("/demo.mp4").headers["content-length"] == "4096"
        assert client.get("/../.env").status_code == 404
