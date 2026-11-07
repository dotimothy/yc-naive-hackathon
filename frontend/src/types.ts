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

export type Keypoint = { name: string; x: number; y: number; visibility: number };

export type TrackSample = {
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
  | EnvelopeOf<'clip.updated', Clip>
  | EnvelopeOf<'event.created', GameEvent>
  | EnvelopeOf<'audit.created', unknown>;

type EnvelopeOf<K extends string, P> = {
  sequence?: number;
  kind: K;
  game_id?: string;
  job_id: string | null;
  payload: P;
  server_timestamp_ms: number;
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
export type HighlightEventType = 'visual_activity' | 'made_shot' | 'assist' | 'rebound' | 'steal' | 'block';

export type HighlightPlayer = {
  identity_source?: 'automatic' | 'reviewer' | 'track';
  association_status?: string;
  association_confidence?: number | null;
  player_id: string;
  label: string;
  track_ids: string[];
  identity_confirmed: boolean;
  highlight_score: number | null;
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
