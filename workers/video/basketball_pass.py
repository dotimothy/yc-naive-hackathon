"""Ball/hoop accumulator fed by the shared source decoder; never opens video."""

from collections import Counter
import json
from pathlib import Path
import time

from workers.video.shot_candidates import (
    BallTrajectories,
    stationary_hoops,
    propose_shots,
)


class BasketballPass:
    def __init__(self, metadata, source_hash, output_dir, device, stride, emit):
        from workers.video.basketball_detector import BasketballDetector

        self.metadata = metadata
        self.source_hash = source_hash
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stride = stride
        self.emit = emit
        self.started = time.perf_counter()
        self.detector = BasketballDetector(
            Path(__file__).resolve().parents[2] / "data/models", device
        )
        self.load_seconds = time.perf_counter() - self.started
        self.linker = BallTrajectories()
        self.objects = []
        self.balls = []
        self.pending = []
        self.counts = Counter()
        self.frames = 0
        self.last_flushed_frames = 0
        self.frames_with_ball = 0
        self.seconds = 0.0
        self.last_time = -1
        self.shot_id = "0"

    def begin_shot(self, shot_id, timestamp_ms):
        self.flush()
        self.shot_id = str(shot_id)
        self.linker = BallTrajectories()

    def snapshot(self):
        return dict(
            integrated=True,
            source_sha256=self.source_hash,
            metadata=self.metadata,
            model=self.detector.manifest,
            device=self.detector.device,
            inference_runtime=getattr(self.detector, "inference_runtime", None),
            stride=self.stride,
            coverage_scope="partial_video",
            analyzed_until_ms=self.last_time,
            processed_frames=self.frames,
            frames_with_ball=self.frames_with_ball,
            stable_hoops=[],
            shot_candidates=[],
            made_shots=None,
            missed_shots=None,
            official_statistics=False,
            elapsed_seconds=time.perf_counter() - self.started,
            detector_seconds=self.seconds,
            model_load_seconds=self.load_seconds,
            observations_by_class=dict(self.counts),
        )

    def observe(self, frame, timestamp_ms):
        before = time.perf_counter()
        detections = self.detector.detect(frame, timestamp_ms)
        self.seconds += time.perf_counter() - before
        self.counts.update(d["class_name"] for d in detections)
        observed = [d for d in detections if d["class_name"] in ("basketball", "hoop")]
        balls = [d for d in observed if d["class_name"] == "basketball"]
        linked = self.linker.update(balls, timestamp_ms)
        for item in observed:
            item["shot_id"] = self.shot_id
        for item in linked:
            item["shot_id"] = self.shot_id
            item["ball_track_id"] = "s" + self.shot_id + "-" + item["ball_track_id"]
        # Preserve segment IDs for streamed ball observations as well as the final artifact.
        self.objects.extend([d for d in observed if d["class_name"] == "hoop"] + linked)
        self.pending.extend([d for d in observed if d["class_name"] == "hoop"] + linked)
        self.balls.extend(linked)
        self.frames += 1
        self.frames_with_ball += bool(balls)
        self.last_time = timestamp_ms
        if self.frames % 5 == 0:
            self.flush()

    def flush(self):
        # Do not replace the last persisted batch with an empty EOF flush.
        if self.frames <= self.last_flushed_frames:
            return
        # Empty detection batches still advance measured processing progress.
        self.emit(
            "basketball.batch",
            dict(
                items=self.pending,
                processed_frames=self.frames,
                frames_with_ball=self.frames_with_ball,
                source_timestamp_ms=self.last_time,
                elapsed_seconds=time.perf_counter() - self.started,
                detector_seconds=self.seconds,
            ),
        )
        self.pending = []
        self.last_flushed_frames = self.frames

    def finish(self, players, decoded_frames, shots=None):
        self.flush()
        shots = shots or [
            dict(shot_id="0", start_ms=0, end_ms=self.metadata["duration_ms"])
        ]
        hoops, proposals = [], []
        for shot in shots:
            sid = str(shot["shot_id"])
            observed = [d for d in self.objects if d.get("shot_id", "0") == sid]
            scene_hoops = stationary_hoops(
                [d for d in observed if d["class_name"] == "hoop"]
            )
            for hoop in scene_hoops:
                hoop.update(shot_id=sid, hoop_id="s" + sid + "-" + hoop["hoop_id"])
            scene_balls = [d for d in self.balls if d.get("shot_id", "0") == sid]
            scene_players = [p for p in players if str(p.get("shot_id", "0")) == sid]
            candidates = propose_shots(
                scene_balls, scene_hoops, self.metadata["duration_ms"], scene_players
            )
            for candidate in candidates:
                candidate.update(
                    shot_id=sid,
                    start_ms=max(candidate["start_ms"], shot["start_ms"]),
                    end_ms=min(candidate["end_ms"], shot["end_ms"]),
                )
            hoops.extend(scene_hoops)
            proposals.extend(candidates)
        report = self.snapshot()
        report.update(
            coverage_scope="full_video",
            shots=shots,
            stable_hoops=hoops,
            shot_candidates=proposals,
            ball_segments=len({b["ball_track_id"] for b in self.balls}),
            decoded_frames=decoded_frames,
            inference_fps=self.frames / self.seconds if self.seconds else 0,
            status="needs_event_review" if hoops else "needs_hoop_review",
        )
        for name, value in [
            ("objects.json", self.objects),
            ("ball_tracks.json", self.balls),
            ("report.json", report),
        ]:
            target = self.output_dir / name
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(value))
            temporary.replace(target)
        self.emit("basketball.updated", dict(status="succeeded", report=report))
        return report
