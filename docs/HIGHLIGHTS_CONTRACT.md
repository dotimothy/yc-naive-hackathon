# Highlight scoring v1 integration contract

Local only. These are highlight-priority scores, never recruiting rankings. Automatic scores describe visual activity/footage quality; no automatic made-shot/assist/steal claims.

GET `/api/games/{game_id}/highlights` returns:
```
{job_id:string|null, generation_id:string|null, status:'not_generated'|'queued'|'running'|'succeeded'|'failed', error:string|null,
 scoring_version:'visual-v1', score_kind:'highlight_priority',
 players:[{player_id:string,label:string,track_ids:string[],identity_confirmed:boolean,highlight_score:number|null,verified_play_count:number,candidate_count:number,selected_count:number,observed_ms:number}],
 candidates:[{candidate_id:string,player_id:string,track_id:string,start_ms:number,end_ms:number,action_start_ms:number,action_end_ms:number,score:number,automatic_score:number,components:Record<string,number>,reasons:string[],review_status:'pending'|'approved'|'rejected',event_type:'visual_activity'|'made_shot'|'assist'|'rebound'|'steal'|'block',origin:'visual_heuristic'|'reviewer_confirmed',selected:boolean,clip_id:string|null,clip_uri:string|null,qa_status:string|null}],
 roster_groups:[{player_id:string,label:string,track_ids:string[],confirmed:true}],
 max_clips:number,top_per_player:number}
```
`POST /api/games/{game_id}/highlights` body `{max_clips:8,top_per_player:2}`; header `Idempotency-Key`; returns same bundle, HTTP 202. Reuses latest successful analysis tracks, no inference rerun. Poll GET while queued/running (750–1000ms). Also automatically generated after new analysis.

`PUT /api/games/{game_id}/highlights/candidates/{candidate_id}/review` body `{decision:'approved'|'rejected'|'pending',event_type:<enum above>}` returns updated bundle. Only approved reviews may set a basketball outcome; pending/rejected use visual_activity. Approved basketball outcomes get transparent heuristic weights; not official statistics. No automatic approval. Reject removes candidate from selection. Review/group edits clear obsolete rendered selections and return bundle `status:not_generated`; click Generate highlights to render current best picks. Mutations return409 while generation/analysis is active; show error.

`PUT /api/games/{game_id}/highlights/players` body `{players:[{player_id:string,label:string,track_ids:string[],confirmed:true}]}` returns updated bundle. Full replacement list; no overlapping assignments; only known current-job track IDs. Defaults are temporary unconfirmed track groups. Human can group fragments and label a private player (e.g. Jersey12) only by explicitly confirming they match. Never infer named identities. Existing confirmed groups can be removed. Mapping scoped to current analysis job and reset on a new analysis.

Frontend implement `frontend/src/HighlightsPanel.tsx` and needed CSS/types/api changes only. Mount panel in GameRoom using props `{gameId:string,jobId:string|null,analysisActive:boolean,onSeek:(ms:number)=>void,onUpdated:()=>void}`; onUpdated triggers room.refresh after generation finishes. Reset per job. Panel fetches on jobId/status change; poll only while queued/running; abort on unmount. Main clip list may also contain selected highlight clips; separate candidate previews are authoritative current selection. Clear stale clip URLs after edits; non-selected/pending candidates can Seek source for review. Scores 0–100, no score shown for rejected-only/insufficient evidence. Expose score components and source ranges. Show all cards without numbering players as talent ranks.
