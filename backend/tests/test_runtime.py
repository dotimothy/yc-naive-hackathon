import pytest
from workers.video import runtime


def capabilities(monkeypatch, *, cuda=False, count=0, mps=False):
    monkeypatch.setattr(runtime.torch.cuda, "is_available", lambda: cuda)
    monkeypatch.setattr(runtime.torch.cuda, "device_count", lambda: count)
    monkeypatch.setattr(runtime.torch.backends.mps, "is_available", lambda: mps)
    monkeypatch.delenv("SCOUTFILM_DEVICE", raising=False)


def test_device_priority_and_explicit_failure(monkeypatch):
    capabilities(monkeypatch, cuda=True, count=2, mps=True)
    assert runtime.select_device() == "cuda:0"
    assert runtime.select_device("cuda:1") == "cuda:1"
    assert runtime.select_device("mps") == "mps"
    assert runtime.select_device("cpu") == "cpu"
    with pytest.raises(ValueError):
        runtime.select_device("cuda:2")
    capabilities(monkeypatch, mps=True)
    assert runtime.select_device() == "mps"
    with pytest.raises(ValueError):
        runtime.select_device("cuda")
    capabilities(monkeypatch)
    assert runtime.select_device() == "cpu"
    with pytest.raises(ValueError):
        runtime.select_device("mps")


def test_runtime_environment_override(monkeypatch):
    capabilities(monkeypatch, mps=True)
    monkeypatch.setenv("SCOUTFILM_DEVICE", "cpu")
    assert runtime.select_device() == "cpu"
    assert runtime.select_device("mps") == "mps"
    monkeypatch.setenv("SCOUTFILM_DEVICE", "cuda")
    with pytest.raises(ValueError):
        runtime.select_device()


def test_inference_awaits_accelerator_before_cpu_transfer(monkeypatch):
    events = []
    monkeypatch.setattr(
        runtime, "synchronize", lambda device: events.append("sync:" + device)
    )

    class Result:
        def cpu(self):
            events.append("copy")
            return "copied"

    def model(inputs):
        events.append("model")
        return [{"boxes": Result()}]

    output, seconds, copy_seconds = runtime.infer(model, object(), "cuda:0")
    assert output == {"boxes": "copied"}
    assert events == ["sync:cuda:0", "model", "sync:cuda:0", "copy"]
    assert seconds >= 0 and copy_seconds >= 0
