# Reviewed basketball evidence UI contract

Replace prominent per-player 0–100 ratings with a reviewed basketball stat line. Keep `highlight_score` and candidate score only as secondary clip-editing priority. No new automatic performance score.

Every HighlightPlayer gets `basketball_stats`:
```
{scope:'reviewed_events',has_evidence:boolean,reviewed_events_count:number,
 field_goals_made:number,field_goals_attempted:number,free_throws_made:number,free_throws_attempted:number,
 points:number|null,unvalued_makes:number,assists:number,rebounds:number,steals:number,blocks:number,turnovers:number,
 pending_candidates:number,held_events_count:number,
 evidence:[{candidate_id:string,event_type:string,event_time_ms:number,points:number|null,track_id:string,clip_uri:string|null}],
 held_events:[{candidate_id:string,reason:string}]}
```
When has_evidence=false, show dashes / “No reviewed basketball events yet”; never show a zero performance rating. All displayed figures are counts within reviewed evidence, not full-game official totals. No per-minute/per-possession numbers. If points=null, a made field goal lacks its value; show “— · shot value needed”. If no FG/FT attempts show dash rather than 0/0. Stats count all eligible reviewed events, independently of clip budget/top_per_player. Metric buttons can filter/display evidence or seek to supporting source time; every number must be traceable.

Review event types add `missed_shot`, `turnover`, `made_free_throw`, `missed_free_throw`. Keep `made_shot` but label it “Made field goal”; missed_shot label “Missed field goal”. Existing assist/rebound/steal/block/visual_activity stay.

PUT candidate review now accepts existing {decision,event_type} plus:
- `event_time_ms?:number|null` — actual source event time, within candidate start_ms <= t < end_ms. UI expose seconds input, prefill existing review time or action_start_ms, and “Use player time” button from current video playhead. Add prop `getPlayheadMs?:()=>number` to HighlightsPanel; parent callback reads videoRef.current.currentTime without 60fps prop churn.
- `points?:1|2|3|null` — only for made_shot (field goal); unknown value is allowed and points total remains unavailable. Made free throw is always one point; no points for misses/assists/etc.
- `actor_confirmed?:boolean` — explicit checkbox “I confirmed this player performed the play”. An explicitly confirmed player group also establishes attribution. Do not silently tick for automatic associations.
- `distinct_event_confirmed?:boolean` — explicit checkbox for genuinely different same-type events within 1.5s. Potential duplicate close events are held from totals unless confirmed distinct; exact duplicate same-type/same-time events are counted once regardless.

Basketball outcome statistics require approved basketball event + event time + confirmed actor/group. Missing facts stay in held_events; rejected/pending/visual activity never count. API response candidate includes review fields in `review` (optional for old data). On reset/reject send visual_activity with no event metadata. Shot value inputs and actor/time inputs needed only when approving basketball outcomes. If reviewer leaves actor unconfirmed, approval can still save but UI clearly shows held from stats.

Integrating completed Claude tracking adds TrackSample/Player metadata: `team_label:'Team A'|'Team B'|'unknown'`, `role_status:'likely_non_player'|'review_required'|'unknown'`, association fields remain provisional. Display metadata as reviewable, never remove a person automatically. Report includes team_summary/role_flags/merge_proposals, optional types only. Do not edit worker/backend files.

UI ownership: frontend/src/** only. Keep shared package/lockfiles untouched. Need build + practical readable cards, source evidence links and review form. Parent implements backend and tests against this schema.
