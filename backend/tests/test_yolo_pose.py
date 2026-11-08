"""Pose adapter device, coordinate, visibility and model-provenance contracts."""

import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from workers.video import runtime
from scripts import download_pose_model


@pytest.mark.parametrize("count", [0, 2])
def test_yolo_adapter_keeps_coordinates_and_confidence(monkeypatch, count):
    model = object.__new__(runtime.YoloPoseModel)
    model.device = "cpu"
    model.confidence = 0.25
    model.image_size = 1280
    points = torch.zeros((count, 17, 3))
    points[:, :, 0] = 120
    points[:, :, 1] = 80
    points[:, :, 2] = 0.7
    points[:, 0, 2] = 0.2
    captured = {}

    def predict(**kwargs):
        captured.update(kwargs)
        return [
            SimpleNamespace(
                boxes=SimpleNamespace(
                    data=torch.zeros((count, 6)),
                    xyxy=torch.tensor([[10, 20, 100, 200]] * count).reshape(count, 4),
                    conf=torch.full((count,), 0.8),
                ),
                keypoints=SimpleNamespace(data=points),
            )
        ]

    model.model = SimpleNamespace(predict=predict)
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    result = model.predict(frame)
    assert captured["source"] is frame and captured["nms"] is False
    assert (
        captured["device"] == "cpu"
        and captured["imgsz"] == 1280
        and captured["conf"] == 0.25
    )
    assert result["keypoints"].shape == (count, 17, 3)
    assert torch.equal(result["keypoint_scores"], points[:, :, 2])
    assert torch.equal(result["keypoints"][:, :, :2], points[:, :, :2])
    if count:
        assert result["keypoints"][0, 0, 2] == 0 and result["keypoints"][0, 1, 2] == 1
        assert result["boxes"][0].tolist() == [10, 20, 100, 200]


def test_rgb_tensor_tool_input_becomes_bgr_frame():
    model = object.__new__(runtime.YoloPoseModel)
    model.device = "cpu"
    model.confidence = 0.25
    model.image_size = 1280
    captured = {}

    def predict(**kwargs):
        captured.update(kwargs)
        return [
            SimpleNamespace(
                boxes=SimpleNamespace(
                    data=torch.empty((0, 6)),
                    xyxy=torch.empty((0, 4)),
                    conf=torch.empty(0),
                ),
                keypoints=SimpleNamespace(data=torch.empty((0, 17, 3))),
            )
        ]

    model.model = SimpleNamespace(predict=predict)
    rgb = torch.zeros((3, 2, 2))
    rgb[0] = 1
    model.predict(rgb)
    assert captured["source"][0, 0].tolist() == [0, 0, 255]


def test_pose_downloader_verifies_existing_bytes_and_manifest(tmp_path, monkeypatch):
    checkpoint = tmp_path / "yolo26n-pose.pt"
    checkpoint.write_bytes(b"pinned-fixture")
    expected = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    monkeypatch.setattr(download_pose_model, "MODEL_SHA256", expected)
    monkeypatch.setattr(download_pose_model, "MODEL_BYTES", len(b"pinned-fixture"))

    def no_download(*a, **k):
        raise AssertionError("Verified local weights must not require a network")

    monkeypatch.setattr(download_pose_model.urllib.request, "urlopen", no_download)
    assert download_pose_model.download(tmp_path) == checkpoint
    assert (
        json.loads((tmp_path / "yolo26n-pose-manifest.json").read_text())["sha256"]
        == expected
    )


def test_native_mps_refuses_explicit_cpu_fallback(monkeypatch):
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    with pytest.raises(ValueError, match="Disable PYTORCH_ENABLE_MPS_FALLBACK"):
        runtime.YoloPoseModel("mps")


def test_bad_checkpoint_download_is_not_promoted(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr(
        download_pose_model.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(b"wrong-weights"),
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        download_pose_model.download(tmp_path)
    assert not (tmp_path / "yolo26n-pose.pt").exists()
    assert not (tmp_path / "yolo26n-pose.download").exists()


def test_new_analysis_uses_yolo_confidence_default(client, video, monkeypatch):
    from backend.app import main
    from backend.tests.test_local import submit

    monkeypatch.setattr(main.POOL, "submit", lambda *a, **k: None)
    game = submit(client, video).json()
    response = client.post(
        "/api/games/" + game["game_id"] + "/analyze",
        json={},
        headers={"Idempotency-Key": "yolo-default"},
    )
    assert response.status_code == 202
    options = main.get("job", response.json()["job_id"])["options"]
    assert options["confidence"] == 0.25 and options["basketball"] is True


def test_pre_migration_api_jobs_use_new_model_calibration():
    from scripts.run_analysis import normalize_options

    assert normalize_options(dict(confidence=0.75)) == dict(
        confidence=0.25, pose_model=runtime.POSE_MODEL
    )
    assert (
        normalize_options(dict(confidence=0.4, pose_model=runtime.POSE_MODEL))[
            "confidence"
        ]
        == 0.4
    )
