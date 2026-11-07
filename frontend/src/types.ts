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
};

export type GameEvent = {
  attempt?: number;
  event_id: string;
  start_ms: number;
  end_ms: number;
  event_type: string;
  origin: string;
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
