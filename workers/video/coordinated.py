"""Run one coordinated decoder/model worker in the optional basketball environment."""

import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time

from workers.video.pipeline import process_video
from workers.video.cancellation import MARKER, check_cancelled

ROOT = Path(__file__).resolve().parents[2]


def optional_runtime():
    python = Path(
        os.environ.get("SCOUTFILM_BALL_PYTHON", ROOT / ".venv-ball/bin/python")
    )
    if not python.is_file():
        return (
            None,
            "Ball tracking unavailable: run scripts/setup_ball_detector.sh, then Analyze again.",
        )
    for filename in ("ebard-rfdetr-nano-state.pth", "ebard-rfdetr-nano-manifest.json"):
        if not (ROOT / "data/models" / filename).is_file():
            return (
                None,
                "Ball tracking unavailable: download the pinned basketball model, then Analyze again.",
            )
    return python, None


def run_analysis(source, output, options, emit):
    output = Path(output)
    options = dict(options, cancel_path=str(output / MARKER))
    check_cancelled(options["cancel_path"])
    if not options.get("basketball"):
        return process_video(source, output, options, emit)
    python, reason = optional_runtime()
    if reason:
        return process_video(
            source,
            output,
            dict(options, basketball=False, basketball_unavailable=reason),
            emit,
        )
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    messages = queue.Queue()
    timeout = float(os.environ.get("SCOUTFILM_WORKER_TIMEOUT_SECONDS", "3600"))
    with (output / "worker.log").open("w") as log:
        proc = subprocess.Popen(
            [str(python), str(ROOT / "scripts/run_analysis.py")],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            bufsize=1,
            start_new_session=True,
            env=dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK="0"),
        )

        def read():
            try:
                for line in proc.stdout:
                    messages.put(line)
            finally:
                messages.put(None)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        result = None
        try:
            proc.stdin.write(
                json.dumps(
                    dict(source=str(source), output=str(output), options=options)
                )
                + "\n"
            )
            proc.stdin.flush()
            while True:
                check_cancelled(options["cancel_path"])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(proc.args, timeout)
                try:
                    line = messages.get(timeout=min(0.25, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    break
                message = json.loads(line)
                kind, payload = message["kind"], message["payload"]
                if kind == "result":
                    result = payload
                    break
                emit(kind, payload)
                proc.stdin.write(json.dumps(dict(payload=payload)) + "\n")
                proc.stdin.flush()
            code = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            if code or result is None:
                raise RuntimeError(
                    "Coordinated analysis worker failed; check worker.log"
                )
            return result
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            proc.stdin.close()
            reader.join(timeout=1)
            proc.stdout.close()
