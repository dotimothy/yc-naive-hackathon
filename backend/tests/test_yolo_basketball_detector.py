"""Class and geometry contracts, independent of model recognition accuracy."""

import hashlib
import io
import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from workers.video import yolo_basketball_detector as module
from scripts import download_yolo_basketball_model as downloader


def vocabulary():
    names = {i: "unused-" + str(i) for i in range(4585)}
    names.update({310: "basketball", 315: "basketball hoop"})
    return names


def detector(monkeypatch, rows, tiled=False):
    model = object.__new__(module.YoloBasketballDetector)
    model.device = "cpu"
    model.image_size = 1280
    model.thresholds = {"basketball": 0.25, "hoop": 0.35}
    model.class_map = module.verified_class_map(vocabulary())
    model.tiled = tiled
    captured = {}

    def predict(**kwargs):
        captured.update(kwargs)
        return [
            SimpleNamespace(
                boxes=SimpleNamespace(
                    data=torch.tensor(row, dtype=torch.float32).reshape(-1, 6)
                )
            )
            for row in rows
        ]

    model.model = SimpleNamespace(predict=predict)
    monkeypatch.setattr(module, "synchronize", lambda _: None)
    return model, captured


def test_exact_prompt_free_classes_are_required():
    assert module.verified_class_map(vocabulary()) == {
        310: ("basketball", 0),
        315: ("hoop", 1),
    }
    names = vocabulary()
    names[315] = "basketball backboard"
    with pytest.raises(ValueError, match="vocabulary mismatch"):
        module.verified_class_map(names)
    names = {0: "person", 32: "sports ball"}
    with pytest.raises(ValueError, match="vocabulary mismatch"):
        module.verified_class_map(names)
    names = vocabulary()
    names[310] = "basketball player"
    with pytest.raises(ValueError, match="vocabulary mismatch"):
        module.verified_class_map(names)


def test_normalized_boxes_thresholds_and_source_ids(monkeypatch):
    model, captured = detector(
        monkeypatch,
        [
            [
                [10, 20, 30, 60, 0.8, 310],
                [20, 10, 80, 30, 0.7, 315],
                [0, 0, 10, 10, 0.9, 311],
                [0, 0, 10, 10, 0.3, 315],
            ]
        ],
    )
    frame = np.full((100, 100, 3), [10, 20, 30], dtype=np.uint8)
    result = model.detect(frame, 1200)
    assert captured["source"][0][0, 0].tolist() == [10, 20, 30]
    assert captured["classes"] == [310, 315] and captured["nms"] is False
    assert captured["device"] == "cpu" and captured["imgsz"] == 1280
    assert captured["quantize"] is None
    assert len(result) == 2
    ball = next(d for d in result if d["class_name"] == "basketball")
    hoop = next(d for d in result if d["class_name"] == "hoop")
    assert ball["class_id"] == 0 and ball["source_class_id"] == 310
    assert hoop["class_id"] == 1 and hoop["source_class_id"] == 315
    assert ball["bbox"] == pytest.approx([0.1, 0.2, 0.2, 0.4])
    assert ball["timestamp_ms"] == 1200 and ball["verified"] is False
    assert model.last_inference_seconds >= 0 and model.last_transfer_seconds >= 0


def test_tiles_map_back_and_cross_view_duplicates_are_suppressed(monkeypatch):
    # Width200 crops120; right crop starts80. All boxes describe the same source ball.
    model, _ = detector(
        monkeypatch,
        [
            [[90, 20, 100, 30, 0.9, 310]],
            [[90, 20, 100, 30, 0.8, 310]],
            [[10, 20, 20, 30, 0.7, 310]],
        ],
        tiled=True,
    )
    result = model.detect(np.zeros((100, 200, 3), dtype=np.uint8), 0)
    assert len(result) == 1 and result[0]["bbox"] == pytest.approx(
        [0.45, 0.2, 0.05, 0.1]
    )


def test_empty_and_invalid_geometry_do_not_invent_observations(monkeypatch):
    model, _ = detector(monkeypatch, [[]])
    assert model.detect(np.zeros((10, 10, 3), dtype=np.uint8), 0) == []
    model, _ = detector(
        monkeypatch, [[[5, 5, 4, 8, 0.9, 310], [1, 1, float("nan"), 3, 0.9, 315]]]
    )
    assert model.detect(np.zeros((10, 10, 3), dtype=np.uint8), 0) == []
    with pytest.raises(ValueError, match="BGR frame"):
        model.detect(np.zeros((10, 10), dtype=np.uint8), 0)


def test_no_silent_result_view_mismatch(monkeypatch):
    model, _ = detector(monkeypatch, [], tiled=False)
    with pytest.raises(ValueError, match="source views"):
        model.detect(np.zeros((10, 10, 3), dtype=np.uint8), 0)


def test_native_mps_refuses_cpu_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "select_device", lambda _: "mps")
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    with pytest.raises(ValueError, match="Disable PYTORCH_ENABLE_MPS_FALLBACK"):
        module.YoloBasketballDetector(tmp_path, "mps")


def test_downloader_reuses_verified_file_and_records_provenance(tmp_path, monkeypatch):
    data = b"verified-model-fixture"
    (tmp_path / downloader.MODEL_NAME).write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(downloader, "MODEL_BYTES", len(data))
    monkeypatch.setattr(downloader, "MODEL_SHA256", sha)

    def no_network(*a, **k):
        raise AssertionError("No network needed for verified weights")

    monkeypatch.setattr(downloader.urllib.request, "urlopen", no_network)
    assert downloader.download(tmp_path).read_bytes() == data
    manifest = json.loads((tmp_path / downloader.MANIFEST_NAME).read_text())
    assert manifest["sha256"] == sha and manifest["text_encoder_required"] is False
    assert manifest["source_classes"] == {"310": "basketball", "315": "basketball hoop"}


def test_bad_download_is_not_promoted(tmp_path, monkeypatch):
    monkeypatch.setattr(
        downloader.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"invalid")
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        downloader.download(tmp_path)
    assert not (tmp_path / downloader.MODEL_NAME).exists()
    assert not (tmp_path / "yoloe-26n-seg-pf.download").exists()


def test_missing_or_corrupted_weights_fail_before_model_loading(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError, match="download_yolo_basketball_model"):
        module.YoloBasketballDetector(tmp_path, "cpu")
    (tmp_path / downloader.MODEL_NAME).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="state hash mismatch"):
        module.YoloBasketballDetector(tmp_path, "cpu")
