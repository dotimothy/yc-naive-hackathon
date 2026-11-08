import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { api, ApiError } from './api';
import type { AnalysisProgress, Clip, Envelope, GameEvent, Job, TrackSample } from './types';

export type StreamState = 'connecting' | 'live' | 'reconnecting' | 'closed';

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);
export const isTerminal = (job?: Job | null) => !!job && TERMINAL.has(job.status);

type Progress = AnalysisProgress;

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
  // Cancellation is sticky: historical success/failure batches cannot resurrect this attempt.
  if (existing?.status === 'cancelled' && job.attempts <= existing.attempts) return state;
  if (existing?.status === 'cancelling' && (job.status === 'running' || job.status === 'queued') && job.attempts <= existing.attempts) return state;
  if (job.status === 'cancelling' || job.status === 'cancelled') job = { ...job, report: null };
  // Replayed history can deliver stale non-terminal updates after a fetched terminal state.
  if (existing && isTerminal(existing) && !isTerminal(job) && job.attempts <= existing.attempts) return state;
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
  if (job.job_id === next.activeJobId && (job.status === 'cancelling' || job.status === 'cancelled')) {
    next = clearedOutputs(next, next.activeJobId, next.attempt);
  }
  return next;
}

function reducer(state: RoomState, action: Action): RoomState {
  switch (action.type) {
    case 'refreshed': {
      const fetchedJobs = action.jobs.map(job => {
        const previous = state.jobs.find(j => j.job_id === job.job_id);
        if (previous?.status === 'cancelled' && job.attempts <= previous.attempts) return previous;
        if (previous?.status === 'cancelling' && (job.status === 'queued' || job.status === 'running') && job.attempts <= previous.attempts) return previous;
        return job.status === 'cancelled' || job.status === 'cancelling' ? { ...job, report: null } : job;
      });
      const latest = fetchedJobs[fetchedJobs.length - 1] ?? null;
      const latestId = latest?.job_id ?? null;
      const sameScope = latestId === state.activeJobId && (latest?.attempts ?? 0) === state.attempt;
      const tracks = new Map<string, TrackSample>();
      for (const s of action.tracks) tracks.set(sampleKey(s), s);
      // Fetched clips/events are authoritative replacements: highlight review edits and regeneration
      // remove obsolete selections server-side, so stale entries must not linger from earlier state.
      const clips: Record<string, Clip> = {};
      for (const c of action.clips) clips[c.clip_id] = c;
      const events: Record<string, GameEvent> = {};
      for (const e of action.events) events[e.event_id] = e;
      const known = new Set(fetchedJobs.map((j) => j.job_id));
      // Keep any job seen on the stream that the list request did not yet include.
      const jobs = [...fetchedJobs, ...state.jobs.filter((j) => !known.has(j.job_id))];
      const cancelled = latest?.status === 'cancelled' || latest?.status === 'cancelling';
      return {
        ...state,
        jobs,
        activeJobId: jobs[jobs.length - 1]?.job_id ?? null,
        attempt: latest?.attempts ?? 0,
        tracks: cancelled ? new Map() : tracks,
        clips: cancelled ? {} : clips,
        events: cancelled ? {} : events,
        progress: sameScope && !cancelled ? state.progress : null,
        loaded: true,
      };
    }
    case 'job':
      return upsertJob(state, action.job);
    case 'envelope': {
      const env = action.envelope;
      if (env.kind === 'job.updated') return upsertJob(state, env.payload);
      if (env.kind === 'audit.created' || env.kind === 'highlights.updated') return state;
      if (!env.job_id || env.job_id !== state.activeJobId) return { ...state, ignored: state.ignored + 1 };
      // Completed-job snapshots include reviewer corrections; historical raw batches must not undo them.
      if (['cancelling', 'cancelled'].includes(state.jobs.find(j => j.job_id === state.activeJobId)?.status ?? '')) return state;
      if (state.loaded && isTerminal(state.jobs.find(j => j.job_id === state.activeJobId))) return { ...state, ignored: state.ignored + 1 };
      if (env.kind === 'analysis.progress') {
        const attempt = env.payload.attempt ?? state.attempt;
        if (attempt < state.attempt) return { ...state, ignored: state.ignored + 1 };
        const base = attempt > state.attempt ? clearedOutputs(state, state.activeJobId, attempt) : state;
        if (base.progress && env.payload.source_timestamp_ms < base.progress.source_timestamp_ms) return base;
        return { ...base, progress: { ...base.progress, ...env.payload } };
      }
      if (env.kind === 'tracks.batch') {
        const attempt = env.payload.attempt ?? state.attempt;
        if (attempt < state.attempt) return { ...state, ignored: state.ignored + 1 };
        const base = attempt > state.attempt ? clearedOutputs(state, state.activeJobId, attempt) : state;
        const tracks = new Map(base.tracks);
        for (const s of env.payload.items) tracks.set(sampleKey(s), s);
        const progress =
          !base.progress || env.payload.source_timestamp_ms >= base.progress.source_timestamp_ms
            ? { ...base.progress, processed_frames: env.payload.processed_frames, source_timestamp_ms: env.payload.source_timestamp_ms }
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
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const cancelAbort = useRef<AbortController | null>(null);
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
    let correctionTimer: number | undefined;
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
        if (envelope.kind === 'highlights.updated' && openedAt && envelope.server_timestamp_ms >= openedAt - 50) {
          window.clearTimeout(correctionTimer);
          correctionTimer = window.setTimeout(() => void refresh(), 150);
        }
        // Only envelopes written after this connection opened are live; earlier ones are replayed history.
        if (openedAt && envelope.server_timestamp_ms >= openedAt - 50) {
          const delay = Math.max(0, receivedAt - envelope.server_timestamp_ms);
          setDelays((d) => [...d.slice(-49), delay]);
        }
      },
    });
    return () => {
      window.clearTimeout(correctionTimer);
      source.close();
      refreshAbort.current?.abort();
      cancelAbort.current?.abort();
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
    if ((streamState === 'live' && activeJob?.status !== 'cancelling') || !activeJob || isTerminal(activeJob)) return;
    const id = activeJob.job_id;
    const timer = window.setInterval(() => {
      api.getJob(id).then((job) => dispatch({ type: 'job', job })).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [streamState, activeJob]);

  const startAnalysis = useCallback(async () => {
    setStarting(true);
    setStartError(null);
    setCancelError(null);
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

  const cancelAnalysis = useCallback(async () => {
    if (!activeJob || isTerminal(activeJob) || activeJob.status === 'cancelling' || cancelling) return;
    const controller = new AbortController();
    cancelAbort.current?.abort();
    cancelAbort.current = controller;
    setCancelling(true);
    setCancelError(null);
    try {
      const job = await api.cancelAnalysis(activeJob.job_id, controller.signal);
      if (controller.signal.aborted) return;
      dispatch({ type: 'job', job });
      if (isTerminal(job)) void refresh();
    } catch (error) {
      if (!controller.signal.aborted) setCancelError(error instanceof ApiError ? error.message : 'Could not cancel analysis');
    } finally {
      if (cancelAbort.current === controller && !controller.signal.aborted) setCancelling(false);
    }
  }, [activeJob, cancelling, refresh]);

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
    cancelling,
    cancelError,
    cancelAnalysis,
    delayStats,
  };
}
