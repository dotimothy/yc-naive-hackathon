from fastapi.testclient import TestClient


def test_library_status_batches_latest_badges_without_reports_or_removed_games(
    tmp_path, monkeypatch
):
    from backend.app import main

    monkeypatch.setattr(main, "DB", tmp_path / "library.sqlite3")
    main.initialize()
    main.put("game", "g", {"game_id": "g"})
    main.put("game", "removed", {"game_id": "removed", "removed_from_library": True})
    main.put(
        "job",
        "old",
        {
            "game_id": "g",
            "job_id": "old",
            "status": "succeeded",
            "phase": "complete",
            "report": {"private_path": "/private/media"},
        },
    )
    main.put(
        "job",
        "new",
        {
            "game_id": "g",
            "job_id": "new",
            "status": "running",
            "phase": "tracking",
            "report": {"samples": [1, 2, 3]},
        },
    )
    main.put(
        "job", "hidden", {"game_id": "removed", "job_id": "hidden", "status": "failed"}
    )
    response = TestClient(main.app).get("/api/library/status")
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"game_id": "g", "job_id": "new", "status": "running", "phase": "tracking"}
        ]
    }
