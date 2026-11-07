import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { api, ApiError } from './api';
import type { Clip, Envelope, GameEvent, Job, TrackSample } from './types';

export type StreamState = 'connecting' | 'live' | 'reconnecting' | 'closed';

const TERMINAL = new Set(['succeeded', 'failed']);
export const isTerminal = (job?: Job | null) => !!job && TERMINAL.has(job.status);

type Progress = { processed_frames?: number; source_timestamp_ms: number };

type RoomState = {
  jobs: Job[];
  /** Latest job for the game; only its outputs are displayed. */
  activeJobId: string | null;
  /** Attempt whose outputs are currently held; older-attempt batches are dropped. */
  attempt: number;
  tracks: Map<string, TrackSample>;
  clips: Record<string, Clip>;
  events: Record<string, GameEvent>;
  progress: Progress | null;
  ignored: number;
  loaded: boolean;
};

type Action =
  | { type: 'refreshed'; jobs: Job[]; tracks: TrackSample[]; clips: Clip[]; events: GameEvent[] }
  | { type: 'job'; job: Job }
  | { type: 'envelope'; envelope: Envelope };

const initial: RoomState = {
  jobs: [],
  activeJobId: null,
  attempt: 0,
  tracks: new Map(),
  clips: {},
  events: {},
  progress: null,
  ignored: 0,
  loaded: false,
};

const sampleKey = (s: TrackSample) => `${s.timestamp_ms}:${s.track_id}`;

function clearedOutputs(state: RoomState, activeJobId: string | null, attempt: number): RoomState {
  return { ...state, activeJobId, attempt, tracks: new Map(), clips: {}, events: {}, progress: null };
}

function upsertJob(state: RoomState, job: Job): RoomState {
  const index = state.jobs.findIndex((j) => j.job_id === job.job_id);
  const existing = index >= 0 ? state.jobs[index] : undefined;
  // Replayed history can deliver stale non-terminal updates after a fetched terminal state.
  if (existing && isTerminal(existing) && !isTerminal(job)) return state;
  if (existing && existing.attempts > job.attempts) return state;
  const jobs = index >= 0 ? state.jobs.map((j, i) => (i === index ? { ...j, ...job } : j)) : [...state.jobs, job];
  const latest = jobs[jobs.length - 1];
  let next: RoomState = { ...state, jobs };
  if (latest.job_id !== state.activeJobId) {
    next = clearedOutputs(next, latest.job_id, latest.attempts);
  } else if (job.job_id === state.activeJobId && job.attempts > state.attempt) {
    // A retry replaces the job's materialized output on the backend.
    next = clearedOutputs(next, job.job_id, job.attempts);
  }
  return next;
}

function reducer(state: RoomState, action: Action): RoomState {
  switch (action.type) {
    case 'refreshed': {
      const latest = action.jobs[action.jobs.length - 1] ?? null;
      const latestId = latest?.job_id ?? null;
      const sameScope = latestId === state.activeJobId && (latest?.attempts ?? 0) === state.attempt;
      const tracks = new Map(sameScope ? state.tracks : undefined);
      for (const s of action.tracks) tracks.set(sampleKey(s), s);
      // Fetched clips/events are authoritative replacements: highlight review edits and regeneration
      // remove obsolete selections server-side, so stale entries must not linger from earlier state.
      const clips: Record<string, Clip> = {};
      for (const c of action.clips) clips[c.clip_id] = c;
      const events: Record<string, GameEvent> = {};
      for (const e of action.events) events[e.event_id] = e;
      const known = new Set(action.jobs.map((j) => j.job_id));
      // Keep any job seen on the stream that the list request did not yet include.
      const jobs = [...action.jobs, ...state.jobs.filter((j) => !known.has(j.job_id))];
      return {
        ...state,
        jobs,
        activeJobId: jobs[jobs.length - 1]?.job_id ?? null,
        attempt: latest?.attempts ?? 0,
        tracks,
        clips,
        events,
        progress: sameScope ? state.progress : null,
        loaded: true,
      };
    }
    case 'job':
      return upsertJob(state, action.job);
    case 'envelope': {
      const env = action.envelope;
      if (env.kind === 'job.updated') return upsertJob(state, env.payload);
      if (env.kind === 'audit.created') return state;
      if (!env.job_id || env.job_id !== state.activeJobId) return { ...state, ignored: state.ignored + 1 };
      if (env.kind === 'tracks.batch') {
        const attempt = env.payload.attempt ?? state.attempt;
        if (attempt < state.attempt) return { ...state, ignored: state.ignored + 1 };
        const base = attempt > state.attempt ? clearedOutputs(state, state.activeJobId, attempt) : state;
        const tracks = new Map(base.tracks);
        for (const s of env.payload.items) tracks.set(sampleKey(s), s);
        const progress =
          !base.progress || env.payload.source_timestamp_ms >= base.progress.source_timestamp_ms
            ? { processed_frames: env.payload.processed_frames, source_timestamp_ms: env.payload.source_timestamp_ms }
            : base.progress;
        return { ...base, tracks, progress };
      }
      if ((env.kind === 'clip.updated' || env.kind === 'event.created') && (env.payload.attempt ?? state.attempt) < state.attempt) return state;
      if (env.kind === 'clip.updated') return { ...state, clips: { ...state.clips, [env.payload.clip_id]: env.payload } };
      if (env.kind === 'event.created')
        return { ...state, events: { ...state.events, [env.payload.event_id]: env.payload } };
      return state;
    }
  }
}

function newKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export type DelayStats = { last: number; median: number; count: number } | null;

/** State for one game. Mount with key={gameId} so everything resets per game. */
export function useGameRoom(gameId: string) {
  const [state, dispatch] = useReducer(reducer, initial);
  const [streamState, setStreamState] = useState<StreamState>('connecting');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [delays, setDelays] = useState<number[]>([]);
  const pendingKey = useRef<string | null>(null);
  const refreshAbort = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    refreshAbort.current?.abort();
    const controller = new AbortController();
    refreshAbort.current = controller;
    setRefreshing(true);
    try {
      const signal = controller.signal;
      const [jobs, tracks, clips, events] = await Promise.all([
        api.listJobs(gameId, signal),
        api.tracks(gameId, signal),
        api.clips(gameId, signal),
        api.events(gameId, signal),
      ]);
      if (signal.aborted) return;
      dispatch({ type: 'refreshed', jobs, tracks, clips, events });
      setLoadError(null);
    } catch (error) {
      if ((error as Error).name === 'AbortError') return;
      setLoadError(error instanceof ApiError ? error.message : 'Could not load game data');
    } finally {
      if (refreshAbort.current === controller) setRefreshing(false);
    }
  }, [gameId]);

  // Initial load + SSE stream; refresh on every reconnect. Closed on unmount / game change.
  useEffect(() => {
    let openedAt = 0;
    let everOpened = false;
    void refresh();
    const source = api.stream(gameId, {
      onOpen: () => {
        openedAt = Date.now();
        setStreamState('live');
        if (everOpened) void refresh();
        everOpened = true;
      },
      onError: () => setStreamState(source.readyState === EventSource.CLOSED ? 'closed' : 'reconnecting'),
      onEnvelope: (envelope, receivedAt) => {
        dispatch({ type: 'envelope', envelope });
        // Only envelopes written after this connection opened are live; earlier ones are replayed history.
        if (openedAt && envelope.server_timestamp_ms >= openedAt - 50) {
          const delay = Math.max(0, receivedAt - envelope.server_timestamp_ms);
          setDelays((d) => [...d.slice(-49), delay]);
        }
      },
    });
    return () => {
      source.close();
      refreshAbort.current?.abort();
    };
  }, [gameId, refresh]);

  const activeJob = state.jobs.find((j) => j.job_id === state.activeJobId) ?? null;
  const activeStatus = activeJob?.status;

  // Final refresh when the active job transitions to a terminal state, so materialized outputs are authoritative.
  const previousStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    const before = previousStatus.current;
    previousStatus.current = activeStatus;
    if (before && !TERMINAL.has(before) && activeStatus && TERMINAL.has(activeStatus)) void refresh();
  }, [activeStatus, refresh]);

  // Fallback polling by job ID while the stream is down and a job is active.
  useEffect(() => {
    if (streamState === 'live' || !activeJob || isTerminal(activeJob)) return;
    const id = activeJob.job_id;
    const timer = window.setInterval(() => {
      api.getJob(id).then((job) => dispatch({ type: 'job', job })).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [streamState, activeJob]);

  const startAnalysis = useCallback(async () => {
    setStarting(true);
    setStartError(null);
    // Reuse the key if the previous attempt never got a response (safe retry).
    pendingKey.current ??= newKey();
    try {
      const job = await api.analyze(gameId, pendingKey.current);
      pendingKey.current = null;
      setDelays([]);
      dispatch({ type: 'job', job });
    } catch (error) {
      if (error instanceof ApiError && error.status !== 0) pendingKey.current = null;
      setStartError(error instanceof ApiError ? error.message : 'Could not start analysis');
    } finally {
      setStarting(false);
    }
  }, [gameId]);

  let delayStats: DelayStats = null;
  if (delays.length) {
    const sorted = [...delays].sort((a, b) => a - b);
    delayStats = { last: delays[delays.length - 1], median: sorted[Math.floor(sorted.length / 2)], count: delays.length };
  }

  return {
    ...state,
    activeJob,
    streamState,
    loadError,
    refreshing,
    refresh,
    starting,
    startError,
    startAnalysis,
    delayStats,
  };
}
