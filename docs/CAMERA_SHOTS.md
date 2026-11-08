# Camera shots and gameplay review

New analyses inspect each decoded frame with a small CPU hard-cut detector, using the existing single source decoder. Strong colour/structure discontinuities suggest a new shot; translation and small affine-motion checks reject ordinary pans/zoom. This is a heuristic, not camera identity or validated broadcast-edit accuracy.

At a detected boundary, person tracking and anonymous identity association restart before processing the new view. Later-shot track IDs are namespaced (`s1:1`, etc.). Ball trajectories restart, and stationary-hoop consensus, role flags, colour grouping and merge proposals are computed within each shot. A manually supplied court polygon applies only to the first shot because it is invalid after a viewpoint change. Automatic associations never join people across shots; reviewers can still explicitly group checked fragments.

The report and `shots` records carry original half-open source ranges, boundary evidence, visual summaries and reviewable categories:

- `gameplay_candidate`: several small detections spread across a wide view.
- `non_gameplay_candidate`: sustained dominant close-up, or near-uniform frames without people.
- `uncertain`: insufficient evidence. Missing detections alone do not establish non-gameplay.

These do not distinguish competitive play from warm-ups or establish basketball events. No source footage is deleted. Non-gameplay and uncertain shots stay out of automatic highlights until confirmed as gameplay. Reviewers can confirm, exclude or reset each shot. Changes clear highlight picks; regeneration uses the updated eligibility.

Highlight evidence, context, spotlights, fallback/coach windows and ball proposals stay within camera-shot boundaries. Regeneration and identity corrections retain the manifest. Ball proposal imports reject actors from a different shot. Video overlays do not carry a prior-shot sample into the new camera view, even if the observation is still inside the usual400/200ms display window.

The Camera Shots panel shows ranges and source seeking. Create shot clip exports a separate, source-verified MP4 for any chosen shot (including an explicitly chosen non-gameplay review shot); this adds no player outcome/statistics. Exports are cached per job/shot. The original upload remains available.

## Implemented API

- `GET /api/games/{game_id}/shots`: current job and live/completed shot ranges.
- `PUT .../shots/{shot_id}/review`, body `{job_id,decision:gameplay|non_gameplay|pending}`: confirmed review override, with stale-job/generation guards.
- `POST .../shots/{shot_id}/clip`, body `{job_id}`: render and verify this shot, return a registered media URI. Repeated exports reuse the clip.

Worker `shots.updated` messages persist live boundaries; the final report adds `shots`, `shot_detection_seconds`, the detector method and per-shot identity summaries. Legacy runs remain readable and need re-analysis to add shot metadata.

## Validation and limits

CPU unit/integration tests cover static shots, pans, exposure flashes, hard cuts, classification uncertainty, all-frame inspection with unchanged model cadence, tracker namespace resets, ball/hoop reset, clip-range clamping, eligibility/reviewer overrides, stale-job rejection, cross-camera actor rejection and playable/idempotent shot export.

The authorized outdoor12-second and indoor8.4-second fixed-camera sources each produced one shot (zero false cuts). A deliberately joined outdoor→indoor derivative detected its boundary exactly at2000ms. A full MPS worker run on that4-second derivative produced two gameplay candidates, disjoint identity IDs and four source-verified highlight clips within their own shot. The first camera shot was also manually exported as a playable0–2000ms review clip. Browser checks cover review, export, stale responses, live polling, mobile layout and hiding old overlays across an unsampled cut.

These tests validate bookkeeping and limited examples, not broadcast-cut precision/recall or gameplay-classification accuracy. Fades, very rapid edits under500ms and sustained uniform exposures can be missed. Human review remains necessary.
