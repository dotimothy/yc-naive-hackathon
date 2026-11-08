import hashlib
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from workers.video import basketball_detector as module


@pytest.fixture
def detector_factory(tmp_path, monkeypatch):
    weights = b"test weights"
    (tmp_path / "ebard-rfdetr-nano-state.pth").write_bytes(weights)
    (tmp_path / "ebard-rfdetr-nano-manifest.json").write_text(
        json.dumps(
            dict(
                sha256=module.MODEL_SHA,
                revision=module.REVISION,
                sanitized_sha256=hashlib.sha256(weights).hexdigest(),
            )
        )
    )
    monkeypatch.setattr(module, "select_device", lambda device: device)
    monkeypatch.setattr(module, "synchronize", lambda device: None)

    class Model:
        fail_setup = False
        fail_predict = False

        def __init__(self, **kwargs):
            self.optimized = False
            self.batch_sizes = []

        def optimize_for_inference(self, **kwargs):
            self.optimized = True
            self.settings = kwargs
            if self.fail_setup:
                raise RuntimeError("unsupported precision")

        def remove_optimized_model(self):
            self.optimized = False

        def predict(self, images, threshold):
            self.batch_sizes.append(len(images))
            if self.optimized and self.fail_predict:
                raise RuntimeError("unsupported operation")
            return [
                SimpleNamespace(xyxy=[], class_id=[], confidence=[]) for _ in images
            ]

    monkeypatch.setitem(sys.modules, "rfdetr", SimpleNamespace(RFDETRNano=Model))
    return lambda device="mps", **kw: module.BasketballDetector(
        tmp_path, device, **kw
    ), Model


def test_half_precision_default_supports_view_count_changes(detector_factory):
    factory, _ = detector_factory
    d = factory()
    assert d.model.settings == dict(compile=False, dtype=torch.float16)
    assert d.inference_runtime["precision"] == "float16"
    d.detect(np.zeros((20, 40, 3), dtype=np.uint8), 0)
    d.detect(np.zeros((40, 20, 3), dtype=np.uint8), 100)
    assert d.model.batch_sizes == [3, 1]


def test_cpu_and_explicit_float32_retain_baseline(detector_factory):
    factory, _ = detector_factory
    assert not factory("cpu").model.optimized
    assert not factory(precision="float32").model.optimized


def test_failed_setup_restores_original_model(detector_factory):
    factory, model = detector_factory
    model.fail_setup = True
    d = factory()
    assert not d.model.optimized
    assert d.inference_runtime["precision"] == "float32"
    assert d.inference_runtime["fallback_reason"] == "RuntimeError"


def test_failed_half_prediction_retries_once_at_float32(detector_factory):
    factory, model = detector_factory
    model.fail_predict = True
    d = factory()
    assert d.detect(np.zeros((20, 40, 3), dtype=np.uint8), 0) == []
    assert d.model.batch_sizes == [3, 3]
    assert d.inference_runtime["precision"] == "float32"
