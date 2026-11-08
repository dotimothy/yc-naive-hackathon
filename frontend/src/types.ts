// Types mirror the payloads produced by backend/app/main.py and workers/video/pipeline.py.
// All *_ms fields refer to the original source video timeline.

export type Game = {
  game_id: string;
  sport: string;
  title: string;
  source_uri: string;
  source_sha256?: string;
  width: number;
  height: number;
  fps: number;
  duration_ms: number;
  frame_count?: number;
  codec?: string;
  sample?: boolean;
};

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export type IdentityMergeSuggestion = {
  track_id: string; timestamp_ms: number; status: 'needs_review'; requires_review: true;
  candidates: { player_id: string; player_label: string; track_ids: string[]; score: number; gap_ms: number }[];
  excluded_candidates?: { player_id: string; reason: string }[];
};

export type JobReport = {
  device?: string;
  execution_mode?: string;
  precision?: string;
  model_cache_hit?: boolean;
  model_load_seconds?: number;
  processed_frames: number;
  decoded_frames?: number;
  track_samples: number;
  track_ids: number;
  inference_fps: number;
  elapsed_seconds: number;
  realtime_factor: number;
  stride: number;
  source_fps?: number;
  model?: string;
  tracker?: string;
  /** Optional tracker review metadata (anonymous colour grouping, role flags, merge proposals). Heuristic, never facts. */
  team_summary?: Record<string, unknown> | null;
  role_flags?: unknown[] | Record<string, unknown> | null;
  merge_proposals?: unknown[] | null;
  identity_merge_suggestions?: IdentityMergeSuggestion[];
};

export type Artifact = { artifact_id: string; kind: string; uri: string; sha256: string };

export type Job = {
  job_id: string;
  game_id: string;
  status: JobStatus;
  phase: string;
  attempts: number;
  error: string | null;
  artifacts?: Artifact[];
  report?: JobReport;
};

export type Keypoint = { name: string; x: number; y: number; visibility: number; confidence?: number };

/** Anonymous colour grouping by first appearance — never a real team or a confirmed fact. */
export type TeamLabel = 'Team A' | 'Team B' | 'unknown';
/** Review flag only; a flagged person is never removed automatically. */
export type RoleStatus = 'likely_non_player' | 'review_required' | 'unknown';

export type TrackSample = {
  team_label?: TeamLabel | null;
  role_status?: RoleStatus | null;
  player_id?: string;
  player_label?: string;
  raw_track_id?: string;
  identity_status?: 'new' | 'continued' | 'reidentified' | 'ambiguous';
  association_confidence?: number | null;
  game_id?: string;
  track_id: string;
  timestamp_ms: number;
  /** Normalized [x, y, w, h] relative to source frame dimensions. */
  bbox: [number, number, number, number];
  confidence: number;
  keypoints: Keypoint[];
  origin?: string;
};

export type Clip = {
  attempt?: number;
  clip_id: string;
  event_id?: string;
  job_id?: string;
  uri: string;
  source_start_ms: number;
  source_end_ms: number;
  qa_status: string;
  identity_verified: boolean;
  measured_duration_ms?: number;
  expected_duration_ms?: number;
  tolerance_ms?: number;
  decoded?: boolean;
  /** 'highlight' for rendered highlight picks; absent/other for review windows. */
  kind?: string;
  track_id?: string | null;
  /** Highlight priority score 0–100 (visual heuristic + reviewer weights). Not a talent rating. */
  highlight_score?: number | null;
  candidate_id?: string | null;
  review_status?: HighlightReviewStatus | null;
};

/** Known origins; backend may add more, so string stays allowed. */
export type EventOrigin = 'review_window' | 'coach_annotation' | 'visual_heuristic' | 'reviewer_confirmed';

export type GameEvent = {
  attempt?: number;
  event_id: string;
  start_ms: number;
  end_ms: number;
  event_type: string;
  origin: EventOrigin | (string & {});
  verified: boolean;
  track_id: string | null;
  clip_uri: string | null;
  evidence_uri: string | null;
};

export type Envelope =
  | EnvelopeOf<'job.updated', Job>
  | EnvelopeOf<'tracks.batch', TracksBatch>
  | EnvelopeOf<'analysis.progress', AnalysisProgress>
  | EnvelopeOf<'clip.updated', Clip>
  | EnvelopeOf<'event.created', GameEvent>
  | EnvelopeOf<'audit.created', unknown>
  | EnvelopeOf<'highlights.updated', HighlightBundle>;

type EnvelopeOf<K extends string, P> = {
  sequence?: number;
  kind: K;
  game_id?: string;
  job_id: string | null;
  payload: P;
  server_timestamp_ms: number;
};

export type AnalysisProgress = {
  processed_frames?: number;
  basketball_processed_frames?: number;
  source_timestamp_ms: number;
  source_frame?: number;
  attempt?: number;
};

export type TracksBatch = {
  items: TrackSample[];
  source_timestamp_ms: number;
  source_frame?: number;
  processed_frames?: number;
  attempt?: number;
};

export type Items<T> = { items: T[] };

// ---- Highlights (docs/HIGHLIGHTS_CONTRACT.md). Scores are highlight priority, never recruiting rankings. ----

export type HighlightStatus = 'not_generated' | 'queued' | 'running' | 'succeeded' | 'failed';
export type HighlightReviewStatus = 'pending' | 'approved' | 'rejected';
export type HighlightEventType =
  | 'visual_activity'
  | 'made_shot'
  | 'missed_shot'
  | 'made_free_throw'
  | 'missed_free_throw'
  | 'assist'
  | 'rebound'
  | 'steal'
  | 'block'
  | 'turnover';

/** Saved reviewer facts for a candidate (docs/REVIEWED_STATS_CONTRACT.md). Optional for old data. */
export type CandidateReview = {
  decision?: HighlightReviewStatus;
  event_type?: HighlightEventType;
  /** Actual source event time; start_ms <= t < end_ms. */
  event_time_ms?: number | null;
  /** Only for made_shot (field goal); null = value unknown. */
  points?: 1 | 2 | 3 | null;
  actor_confirmed?: boolean;
  distinct_event_confirmed?: boolean;
};

export type ReviewRequest = {
  decision: HighlightReviewStatus;
  event_type: HighlightEventType;
  event_time_ms?: number | null;
  points?: 1 | 2 | 3 | null;
  actor_confirmed?: boolean;
  distinct_event_confirmed?: boolean;
};

export type StatEvidence = {
  candidate_id: string;
  event_type: string;
  event_time_ms: number;
  points: number | null;
  track_id: string;
  clip_uri: string | null;
};

/** Counts within reviewed evidence only — not full-game official totals. */
export type BasketballStats = {
  scope: 'reviewed_events';
  has_evidence: boolean;
  reviewed_events_count: number;
  field_goals_made: number;
  field_goals_attempted: number;
  free_throws_made: number;
  free_throws_attempted: number;
  points: number | null;
  unvalued_makes: number;
  assists: number;
  rebounds: number;
  steals: number;
  blocks: number;
  turnovers: number;
  pending_candidates: number;
  held_events_count: number;
  evidence: StatEvidence[];
  held_events: { candidate_id: string; reason: string }[];
};

export type HighlightPlayer = {
  identity_source?: 'automatic' | 'reviewer' | 'track';
  association_status?: string;
  association_confidence?: number | null;
  player_id: string;
  label: string;
  track_ids: string[];
  identity_confirmed: boolean;
  /** Editorial clip priority only; never shown as a player rating. */
  highlight_score: number | null;
  basketball_stats?: BasketballStats | null;
  team_label?: TeamLabel | null;
  role_status?: RoleStatus | null;
  verified_play_count: number;
  candidate_count: number;
  selected_count: number;
  observed_ms: number;
};

export type HighlightCandidate = {
  candidate_id: string;
  player_id: string;
  track_id: string;
  start_ms: number;
  end_ms: number;
  action_start_ms: number;
  action_end_ms: number;
  score: number;
  automatic_score: number;
  components: Record<string, number>;
  reasons: string[];
  review_status: HighlightReviewStatus;
  event_type: HighlightEventType;
  origin: 'visual_heuristic' | 'reviewer_confirmed';
  selected: boolean;
  clip_id: string | null;
  clip_uri: string | null;
  qa_status: string | null;
  review?: CandidateReview | null;
};

export type RosterGroup = { player_id: string; label: string; track_ids: string[]; confirmed: true };

export type HighlightBundle = {
  job_id: string | null;
  generation_id: string | null;
  status: HighlightStatus;
  error: string | null;
  scoring_version: string;
  score_kind: string;
  players: HighlightPlayer[];
  candidates: HighlightCandidate[];
  roster_groups: RosterGroup[];
  max_clips: number;
  top_per_player: number;
};
