# Per-player highlight scoring and automatic clipping

## Workflow

1. Load a sample or upload authorized basketball footage and run analysis. Tracking automatically generates and scores candidate moments, selects picks and renders source-linked clips.
2. Open **Highlight review**. Scores and components explain why footage was selected. **Seek source** selects the relevant track in the main overlay and moves to the action time.
3. If tracking fragmented one person into several IDs, use **Reviewer-confirmed groups** to group them under a private label after checking the footage. This is scoped to the current analysis; new analysis does not reuse track identities.
4. Approve a candidate as visual activity, or confirm an actual basketball outcome after watching it. Reject unsuitable moments. These edits invalidate previously rendered selections.
5. Click **Generate highlights** to render the updated picks from existing tracking, without running pose inference again. Set global clip budget and per-player limit.

Only private local track grouping is implemented; no public profiles or recruitment decisions are generated. Without a confirmed grouping, cards refer to temporary track IDs rather than verified athletes.

## Transparent visual-v1 score

Automatic scoring identifies potentially useful footage from person tracking/pose. It does not detect ball possession, made baskets, assists or steals.

| Component | Maximum contribution | Evidence |
| --- | ---: | --- |
| Movement | 35 | 75th-percentile image-relative track displacement rate, normalized by box height and capped against a fixed heuristic scale |
| Raised-arm pose | 20 | Fraction of observed poses with a visible wrist above its shoulder |
| Detection quality | 25 | Mean person detection confidence |
| Track coverage | 20 | Observed samples relative to expected sampled-frame coverage |

Sum gives a 0–100 **highlight-priority score**, not an athletic ability estimate. Detection confidence is not the probability that a basketball play occurred. A movement-plus-raised-arm contribution below six points produces no candidate; static footage alone cannot earn a candidate from detection confidence. Short tracks require at least four samples spanning one second. Tracking gaps greater than 2.5 sample intervals split evidence segments.

Evidence windows last up to four seconds and advance by two seconds. Exported clips preserve 1.5 seconds before and two seconds after the evidence when available, clamped to the source duration. Movement is in image coordinates; it is not calibrated physical speed, distance, effort or tactical value. Pose and track errors can affect priority.

## Reviewer-confirmed outcomes

A reviewer may approve `made_shot`, `assist`, `rebound`, `steal` or `block`. No model invents these labels. The experimental score becomes `0.8 × outcome_weight + 0.2 × automatic_score`.

Current configurable-in-code weights: made shot 90, assist 85, rebound 70, steal 85, block 85. These express editorial clip preference, not official basketball value or a validated performance model. Approved visual activity keeps its automatic score. Rejected candidates never enter selection and cannot contribute to a player's score. Pending/rejected reviews cannot claim a basketball outcome through the API.

## Selection and player cards

Candidates are sorted by priority inside each confirmed group or temporary track. Overlap of 50% or more of the shorter clip suppresses the lower-scoring same-player candidate. One pick per group is allocated before additional picks, within a default total budget of eight clips and two per player (limits: 12 total, three per player). Clip budgets may leave some groups without a rendered pick.

The player card score is the mean of up to three eligible, non-overlapping picks under the per-player limit, before the global render budget. Insufficient or rejected-only evidence yields no score rather than a misleading zero. Observed duration uses the union of sampled timestamps across grouped tracks; it is not official playing time. Reviewer-confirmed play counts refer to eligible non-overlapping reviewed picks, not box-score totals. Cards are listed by label, not ranked by player merit.

## Persistence and QA

SQLite retains candidates, components, reviews, groups and the current generation per analysis. Mutations are blocked while analysis/generation runs. Request keys prevent duplicate generation; stale candidate IDs and unknown/duplicate track assignments are rejected. Interrupted generations fail visibly on restart and can be regenerated. Rejected/obsolete selections disappear from current clips; historical artifacts remain local for provenance.

A yellow marker briefly follows the selected track during the first 1.5 seconds of observed action, using actual sampled boxes without extrapolation across gaps. The full court view is retained. Every selected clip is re-encoded from the original source and independently checked for valid bounds, measured duration, decoding, source/output hashes and first-frame alignment. Media remains ignored by Git. APIs and UI contract: [HIGHLIGHTS_CONTRACT.md](HIGHLIGHTS_CONTRACT.md).

## Actual validation

On the 12.012-second TrackID3x3 outdoor sample: 26 provisional candidates, eight selected clips from eight temporary tracks; all eight passed QA. A fresh accelerated analysis including scoring and rendering completed in 20.44 seconds locally. Regenerating all eight marked clips from cached tracks took 4.71 seconds. No made-shot/assist/steal labels were inferred. Fourteen backend tests cover scoring evidence, budget/overlap, rejection, verified-outcome weighting, track grouping, regeneration, persistence, media checks, stale-analysis and busy-state boundaries and rendered target-marker pixels, plus existing runtime/API behavior. Browser checks passed for player-focused seeking, rejection/reset, generation from cached tracks, clip playback and 390px layout without horizontal overflow or JavaScript errors.

Tracking continuity and non-athlete filtering still need improvement. Ball/hoop detection and event-recognition evaluation are the next analytics steps before automatic basketball outcome scoring can be claimed. Naive and sponsor integrations remain deferred.
