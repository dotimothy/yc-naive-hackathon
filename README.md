# PlayLattice

Private YC Naive Hackathon prototype; product name is PlayLattice.

Build a sports video intelligence platform, starting with basketball. The first local workflow is video → pose/tracking → replay dashboard → source-verified clips, followed by private opt-in athlete profiles. Keep shared media processing reusable across sports; validate basketball first. Naive orchestration and sponsors follow after the local core is accepted.

- [YOLO26 pose model validation](docs/YOLO26_POSE_VALIDATION.md)
- [Unified player and ball analysis](docs/UNIFIED_ANALYSIS.md)
- [Reviewed basketball statistics and attribution](docs/REVIEWED_STATS_CONTRACT.md)
- [Ball and hoop detector setup and limitations](docs/BALL_HOOP_HANDOFF.md)
- [Tracking held-out evaluation](docs/TRACKING_HANDOFF.md)
- [Player highlight scoring and automatic clips](docs/HIGHLIGHT_SCORING.md)
- [GPU acceleration and CUDA worker setup](docs/ACCELERATION.md)
- [Run the local prototype](docs/LOCAL_PROTOTYPE.md)
- [Agent instructions and ownership](AGENTS.md)
- [Prototype workstreams and acceptance](docs/PROTOTYPE_PLAN.md)
- [Shared API and worker contract](docs/API_CONTRACT.md)
- [TrackID3x3 dataset findings and analysis commands](docs/datasets/TRACKID3X3.md)
- [Planning document](https://docs.google.com/document/d/1kEtq_jr14fybmyRyH_d0h5GALDyeX0YExyt-AdtnJrM/edit)

Current status: local FastAPI/SQLite video pipeline and React film viewer. Accelerated pose/tracking, replayable updates, explainable highlight scores and automatic source-checked clips; preferred sample is a stationary outdoor view of the full 3x3 court. See the local quickstart for setup and current limitations.
