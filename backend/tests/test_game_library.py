import shutil
from backend.app import main


def test_remove_and_restore_preserves_media_and_jobs(client, video):
    stored = main.DATA / "source.mp4"
    shutil.copyfile(video, stored)
    game = main.register_game(stored, "Short test clip")
    game_id = game["game_id"]
    main.put("job", "retained-job", dict(game_id=game_id, status="running"))
    assert client.delete("/api/games/" + game_id).status_code == 200
    assert all(
        g["game_id"] != game_id for g in client.get("/api/games").json()["items"]
    )
    assert stored.exists()
    assert main.get("job", "retained-job")["status"] == "running"
    assert client.delete("/api/games/" + game_id).status_code == 200
    assert client.post("/api/games/" + game_id + "/restore").status_code == 200
    assert any(
        g["game_id"] == game_id for g in client.get("/api/games").json()["items"]
    )
    assert client.delete("/api/games/missing").status_code == 404
