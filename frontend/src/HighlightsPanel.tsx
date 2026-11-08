import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { api, ApiError } from './api';
import { formatMs, trackColor } from './FilmPlayer';
import { PlayerStatsCard, heldReasonText } from './PlayerStatsCard';
import type {
  IdentityMergeSuggestion,
  HighlightBundle,
  HighlightCandidate,
  HighlightEventType,
  HighlightPlayer,
  HighlightReviewStatus,
  HighlightStatus,
  ReviewRequest,
  RosterGroup,
} from './types';

// Contracts: docs/HIGHLIGHTS_CONTRACT.md and docs/REVIEWED_STATS_CONTRACT.md.
// Candidate scores are editorial clip priority for footage review — never basketball performance.
// Player basketball figures come only from reviewer-approved events (basketball_stats).

export type HighlightsPanelProps = {
  identityMergeSuggestions?: IdentityMergeSuggestion[];
  refreshKey?: number;
  gameId: string;
  jobId: string | null;
  analysisActive: boolean;
  onSeek: (ms: number, trackId?: string) => void;
  onUpdated: () => void;
  /** Reads the film's current playhead on demand (no per-frame prop churn). */
  getPlayheadMs?: () => number;
};

const POLL_MS = 900;
const ACTIVE_STATUS = new Set<HighlightStatus>(['queued', 'running']);
const MAX_CLIPS = { min: 1, max: 12, initial: 8 };
const TOP_PER_PLAYER = { min: 1, max: 3, initial: 2 };

export const EVENT_TYPE_LABEL: Record<HighlightEventType, string> = {
  visual_activity: 'Visual activity (no outcome)',
  made_shot: 'Made field goal',
  missed_shot: 'Missed field goal',
  made_free_throw: 'Made free throw',
  missed_free_throw: 'Missed free throw',
  assist: 'Assist',
  rebound: 'Rebound',
  steal: 'Steal',
  block: 'Block',
  turnover: 'Turnover',
};
const EVENT_TYPES = Object.keys(EVENT_TYPE_LABEL) as HighlightEventType[];

const STATUS_LABEL: Record<HighlightStatus, string> = {
  not_generated: 'not generated',
  queued: 'queued',
  running: 'running',
  succeeded: 'generated',
  failed: 'failed',
};

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));
/** Same-type events closer than this are potential duplicates (contract: 1.5 s). */
const DUPLICATE_WINDOW_MS = 1500;

export const isBasketballOutcome = (t: string) => t !== 'visual_activity' && t in EVENT_TYPE_LABEL;
export const eventTypeLabel = (t: string) => EVENT_TYPE_LABEL[t as HighlightEventType] ?? t.replace(/_/g, ' ');

type PointsChoice = '' | '1' | '2' | '3' | 'unknown';
type ReviewDraft = {
  event_type: HighlightEventType;
  /** Seconds in source time, as typed. */
  timeSec: string;
  points: PointsChoice;
  actorConfirmed: boolean;
  distinctConfirmed: boolean;
};

const savedReview = (c: HighlightCandidate) => (c.review && typeof c.review === 'object' ? c.review : undefined);

/** Saved event time if any; never synthesised. */
export function savedEventTimeMs(c: HighlightCandidate): number | null {
  const t = savedReview(c)?.event_time_ms;
  return typeof t === 'number' && Number.isFinite(t) ? t : null;
}

function initialDraft(c: HighlightCandidate): ReviewDraft {
  const r = savedReview(c);
  const event_type = (r?.event_type ?? c.event_type ?? 'visual_activity') as HighlightEventType;
  const t = savedEventTimeMs(c) ?? c.action_start_ms;
  let points: PointsChoice = '';
  if (r?.points === 1 || r?.points === 2 || r?.points === 3) points = String(r.points) as PointsChoice;
  else if (r && r.points === null && event_type === 'made_shot' && c.review_status === 'approved') points = 'unknown';
  return {
    event_type,
    timeSec: (t / 1000).toFixed(2),
    points,
    actorConfirmed: r?.actor_confirmed === true,
    distinctConfirmed: r?.distinct_event_confirmed === true,
  };
}

function parseDraftTime(d: ReviewDraft): number | null {
  const text = d.timeSec.trim();
  if (!text) return null;
  const n = Number(text);
  return Number.isFinite(n) ? Math.round(n * 1000) : null;
}

/** Builds an approval body or explains what is missing. Never fills in actor or shot value. */
function approvalBody(c: HighlightCandidate, d: ReviewDraft): { body?: ReviewRequest; error?: string } {
  if (!isBasketballOutcome(d.event_type)) return { body: { decision: 'approved', event_type: 'visual_activity' } };
  const ms = parseDraftTime(d);
  if (ms === null) return { error: 'Enter the source event time in seconds, or click Use player time.' };
  const boundsError = timeBoundsError(c, ms);
  if (boundsError) return { error: boundsError };
  const body: ReviewRequest = {
    decision: 'approved',
    event_type: d.event_type,
    event_time_ms: ms,
    actor_confirmed: d.actorConfirmed,
    distinct_event_confirmed: d.distinctConfirmed,
  };
  if (d.event_type === 'made_shot') {
    if (!d.points) return { error: 'Choose the field goal value: 1, 2, 3 or Unknown.' };
    body.points = d.points === 'unknown' ? null : (Number(d.points) as 1 | 2 | 3);
  }
  return { body };
}

function timeBoundsError(c: HighlightCandidate, ms: number): string | null {
  if (ms >= c.start_ms && ms < c.end_ms) return null;
  return `Event time ${formatMs(ms)} is outside this candidate's source range ${formatMs(c.start_ms)} – ${formatMs(c.end_ms)} (end excluded). Seek inside the clip or adjust the time.`;
}

function newKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Human-readable outcome label that never presents a heuristic pick as a confirmed play. */
export function outcomeLabel(c: Pick<HighlightCandidate, 'origin' | 'event_type' | 'review_status'>): { text: string; confirmed: boolean } {
  if (c.origin === 'reviewer_confirmed' && c.event_type !== 'visual_activity' && c.review_status === 'approved') {
    return { text: `Reviewer-approved: ${eventTypeLabel(c.event_type)}`, confirmed: true };
  }
  if (c.review_status === 'approved') return { text: 'Reviewer-approved visual activity · no outcome claimed', confirmed: false };
  return { text: 'Provisional visual activity · heuristic, unverified', confirmed: false };
}

/** Reset every piece of state when the analysis job changes by remounting. */
export function HighlightsPanel(props: HighlightsPanelProps) {
  return <HighlightsPanelInner key={`${props.gameId}:${props.jobId ?? 'none'}`} {...props} />;
}

type LoadState = 'loading' | 'ready' | 'missing' | 'error';

function HighlightsPanelInner({ gameId, jobId, analysisActive, onSeek, onUpdated, getPlayheadMs, refreshKey, identityMergeSuggestions = [] }: HighlightsPanelProps) {
  const [bundle, setBundle] = useState<HighlightBundle | null>(null);
  const [load, setLoad] = useState<LoadState>('loading');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pollTick, setPollTick] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [maxClips, setMaxClips] = useState(MAX_CLIPS.initial);
  const [topPerPlayer, setTopPerPlayer] = useState(TOP_PER_PLAYER.initial);
  const [drafts, setDrafts] = useState<Record<string, ReviewDraft>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [playerFilter, setPlayerFilter] = useState<string | null>(null);
  const [candidateLimit, setCandidateLimit] = useState(6);
  const [showAllTracks, setShowAllTracks] = useState(false);
  useEffect(() => setCandidateLimit(6), [playerFilter]);

  // Group mapping form.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [groupLabel, setGroupLabel] = useState('');
  const [groupTracks, setGroupTracks] = useState('');
  const [groupConfirmed, setGroupConfirmed] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const mounted = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const genKey = useRef<string | null>(null);
  const paramsTouched = useRef(false);
  const prevStatus = useRef<HighlightStatus | null>(null);
  const onUpdatedRef = useRef(onUpdated);
  useEffect(() => {
    onUpdatedRef.current = onUpdated;
  }, [onUpdated]);

  const accept = useCallback((b: HighlightBundle) => {
    const before = prevStatus.current;
    prevStatus.current = b.status;
    setBundle(b);
    if (!paramsTouched.current) {
      if (Number.isFinite(b.max_clips)) setMaxClips(clamp(Math.round(b.max_clips), MAX_CLIPS.min, MAX_CLIPS.max));
      if (Number.isFinite(b.top_per_player)) setTopPerPlayer(clamp(Math.round(b.top_per_player), TOP_PER_PLAYER.min, TOP_PER_PLAYER.max));
    }
    // Generation finished (success or failure): refresh the room so the main clip list is authoritative.
    if (before && ACTIVE_STATUS.has(before) && !ACTIVE_STATUS.has(b.status)) onUpdatedRef.current();
  }, []);

  const fetchBundle = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const b = await api.highlights(gameId, controller.signal);
      if (controller.signal.aborted || !mounted.current) return;
      accept(b);
      setLoad('ready');
      setLoadError(null);
    } catch (error) {
      if ((error as Error).name === 'AbortError' || !mounted.current) return;
      if (error instanceof ApiError && error.status === 404) {
        setBundle(null);
        prevStatus.current = null;
        setLoad('missing');
        setLoadError(null);
        return;
      }
      setLoad((l) => (l === 'ready' ? 'ready' : 'error'));
      setLoadError(error instanceof ApiError ? error.message : 'Could not load highlights');
      // Keep polling through transient failures while generation was active.
      if (prevStatus.current && ACTIVE_STATUS.has(prevStatus.current)) setPollTick((t) => t + 1);
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [gameId, accept]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      abortRef.current?.abort();
    };
  }, []);

  // Fetch on mount and whenever analysis status flips. When analysis just completed, re-check
  // shortly after in case the automatic generation was not yet enqueued at the first read.
  const wasActive = useRef(analysisActive);
  useEffect(() => {
    const justFinished = wasActive.current && !analysisActive;
    wasActive.current = analysisActive;
    void fetchBundle();
    if (!justFinished) return;
    const timer = window.setTimeout(() => void fetchBundle(), 1500);
    return () => window.clearTimeout(timer);
  }, [analysisActive, fetchBundle, refreshKey]);

  // Poll only while generation is queued/running.
  const generating = !!bundle && ACTIVE_STATUS.has(bundle.status);
  useEffect(() => {
    if (!generating) return;
    const timer = window.setTimeout(() => void fetchBundle(), POLL_MS);
    return () => window.clearTimeout(timer);
  }, [generating, bundle, pollTick, fetchBundle]);

  const reportError = (error: unknown, fallback: string) => {
    if (error instanceof ApiError && error.status === 409) {
      setActionError(`Not applied: ${error.message}. Analysis or highlight generation is active — wait for it to finish, then retry.`);
      void fetchBundle();
    } else if (error instanceof ApiError && error.status === 404) {
      setActionError(`Not found: ${error.message}. Highlights may not exist for the current analysis yet.`);
      void fetchBundle();
    } else {
      setActionError(error instanceof ApiError ? error.message : fallback);
    }
  };

  /** Runs a mutation that returns an updated bundle. Returns true on success. */
  const mutate = async (label: string, fn: () => Promise<HighlightBundle>, fallback: string): Promise<boolean> => {
    setBusy(label);
    setActionError(null);
    abortRef.current?.abort(); // a stale in-flight GET must not overwrite the mutation result
    try {
      const b = await fn();
      if (!mounted.current) return false;
      accept(b);
      setLoad('ready');
      setLoadError(null);
      return true;
    } catch (error) {
      if (mounted.current) reportError(error, fallback);
      return false;
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const selectionCleared = (what: string) => {
    setNotice(
      `${what} saved. Previously rendered highlight clips were cleared because they no longer match the current picks — click Generate highlights to render the selected clips again.`,
    );
    onUpdatedRef.current(); // drop obsolete highlight clips from the main clip list
  };

  const generate = async () => {
    // Reuse the key if the previous attempt never got a response (network retry is idempotent).
    genKey.current ??= newKey();
    const key = genKey.current;
    setBusy('generate');
    setActionError(null);
    setNotice(null);
    abortRef.current?.abort();
    try {
      const b = await api.generateHighlights(gameId, { max_clips: maxClips, top_per_player: topPerPlayer }, key);
      genKey.current = null;
      if (!mounted.current) return;
      accept(b);
      setLoad('ready');
      setLoadError(null);
    } catch (error) {
      if (error instanceof ApiError && error.status !== 0) genKey.current = null;
      if (mounted.current) reportError(error, 'Could not start highlight generation');
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const setParam = (setter: (n: number) => void, range: { min: number; max: number }) => (raw: string) => {
    paramsTouched.current = true;
    genKey.current = null; // a different body must not reuse an earlier idempotency key
    const n = Number.parseInt(raw, 10);
    setter(Number.isFinite(n) ? clamp(n, range.min, range.max) : range.min);
  };

  const draftFor = (c: HighlightCandidate) => drafts[c.candidate_id] ?? initialDraft(c);
  const setRowError = (id: string, msg: string | null) =>
    setRowErrors(({ [id]: _drop, ...rest }) => (msg ? { ...rest, [id]: msg } : rest));

  const review = async (c: HighlightCandidate, decision: HighlightReviewStatus) => {
    let body: ReviewRequest;
    if (decision === 'approved') {
      const built = approvalBody(c, draftFor(c));
      if (!built.body) return setRowError(c.candidate_id, built.error ?? 'Review incomplete');
      body = built.body;
    } else {
      // Reject/reset clears outcome facts: no event metadata.
      body = { decision, event_type: 'visual_activity' };
    }
    setRowError(c.candidate_id, null);
    const ok = await mutate(`review:${c.candidate_id}`, () => api.reviewHighlight(gameId, c.candidate_id, body), 'Could not save review');
    if (!ok) return;
    // Saved values are read back from candidate.review.
    setDrafts(({ [c.candidate_id]: _drop, ...rest }) => rest);
    selectionCleared('Review');
  };

  // ----- derived data -----
  const players = useMemo(() => {
    const list = bundle?.players ?? [];
    // Reviewed evidence first, then confirmed groups, then longest observed sample. Clip priority is not used.
    const reviewed = (p: HighlightPlayer) => (p.basketball_stats?.has_evidence ? p.basketball_stats.reviewed_events_count : 0);
    return [...list].sort(
      (a, b) =>
        reviewed(b) - reviewed(a) ||
        Number(b.identity_confirmed) - Number(a.identity_confirmed) ||
        b.observed_ms - a.observed_ms ||
        a.label.localeCompare(b.label, undefined, { numeric: true }),
    );
  }, [bundle]);
  const statsAvailable = useMemo(() => (bundle?.players ?? []).some((p) => !!p.basketball_stats), [bundle]);
  const candidateById = useMemo(() => new Map((bundle?.candidates ?? []).map((c) => [c.candidate_id, c])), [bundle]);
  /** candidate_id → why it is held from basketball totals / whether it is counted. */
  const statusByCandidate = useMemo(() => {
    const held = new Map<string, string>();
    const counted = new Set<string>();
    for (const p of bundle?.players ?? []) {
      for (const h of p.basketball_stats?.held_events ?? []) held.set(h.candidate_id, h.reason);
      for (const e of p.basketball_stats?.evidence ?? []) counted.add(e.candidate_id);
    }
    return { held, counted };
  }, [bundle]);
  /** Other approved same-type candidates within the duplicate window of a draft time. */
  const nearbySameType = (c: HighlightCandidate, type: HighlightEventType, ms: number | null): HighlightCandidate[] => {
    if (ms === null || !isBasketballOutcome(type)) return [];
    const out: HighlightCandidate[] = [];
    for (const o of bundle?.candidates ?? []) {
      if (o.candidate_id === c.candidate_id || o.review_status !== 'approved') continue;
      const oType = savedReview(o)?.event_type ?? o.event_type;
      const oMs = savedEventTimeMs(o);
      if (oType === type && oMs !== null && Math.abs(oMs - ms) < DUPLICATE_WINDOW_MS) out.push(o);
    }
    return out;
  };

  // Drop drafts/errors for candidates that no longer exist (new generation).
  useEffect(() => {
    const ids = new Set((bundle?.candidates ?? []).map((c) => c.candidate_id));
    const prune = <T,>(m: Record<string, T>) => {
      const keys = Object.keys(m);
      return keys.every((k) => ids.has(k)) ? m : Object.fromEntries(keys.filter((k) => ids.has(k)).map((k) => [k, m[k]]));
    };
    setDrafts(prune);
    setRowErrors(prune);
  }, [bundle?.generation_id, bundle?.candidates]);
  const playerById = useMemo(() => new Map((bundle?.players ?? []).map((p) => [p.player_id, p])), [bundle]);
  const roster = bundle?.roster_groups ?? [];
  const knownTracks = useMemo(() => {
    const s = new Set<string>();
    for (const p of bundle?.players ?? []) for (const t of p.track_ids) s.add(String(t));
    for (const c of bundle?.candidates ?? []) s.add(String(c.track_id));
    return s;
  }, [bundle]);
  const candidates = useMemo(() => {
    const list = (bundle?.candidates ?? []).filter((c) => !playerFilter || c.player_id === playerFilter);
    // Score descending; rejected candidates are excluded from selection so they sink to the end.
    return list.sort(
      (a, b) => Number(a.review_status === 'rejected') - Number(b.review_status === 'rejected') || b.score - a.score || a.start_ms - b.start_ms,
    );
  }, [bundle, playerFilter]);

  const locked = analysisActive || generating || busy !== null;
  const canMutate = !locked && !!bundle && load === 'ready';
  const jobMismatch = !!bundle?.job_id && !!jobId && bundle.job_id !== jobId;
  const showClips = bundle?.status === 'succeeded';

  // ----- group mapping -----
  const resetForm = () => {
    setEditingId(null);
    setGroupLabel('');
    setGroupTracks('');
    setGroupConfirmed(false);
    setFormError(null);
  };

  const editGroup = (g: RosterGroup) => {
    setEditingId(g.player_id);
    setGroupLabel(g.label);
    setGroupTracks(g.track_ids.join(', '));
    setGroupConfirmed(false);
    setFormError(null);
  };

  const addTracksToForm = (ids: string[]) => {
    const current = parseTracks(groupTracks);
    setGroupTracks([...new Set([...current, ...ids.map(String)])].join(', '));
    setGroupConfirmed(false);
  };

  const submitGroup = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);
    const label = groupLabel.trim();
    const ids = parseTracks(groupTracks);
    if (!label) return setFormError('Enter a private label (e.g. Jersey12).');
    if (!ids.length) return setFormError('Enter at least one current track ID.');
    const unknown = knownTracks.size ? ids.filter((t) => !knownTracks.has(t)) : [];
    if (unknown.length) return setFormError(`Unknown track ID${unknown.length > 1 ? 's' : ''} for this analysis: ${unknown.map((t) => `T${t}`).join(', ')}`);
    const others = roster.filter((g) => g.player_id !== editingId);
    const taken = new Map(others.flatMap((g) => g.track_ids.map((t) => [String(t), g.label] as const)));
    const overlap = ids.filter((t) => taken.has(t));
    if (overlap.length) return setFormError(`Already assigned: ${overlap.map((t) => `T${t} → ${taken.get(t)}`).join(', ')}. Remove it from that group first.`);
    if (!groupConfirmed) return setFormError('Confirm that all listed tracks show the same player.');
    const group: RosterGroup = { player_id: editingId ?? `group-${newKey().slice(0, 8)}`, label, track_ids: ids, confirmed: true };
    const ok = await mutate('players', () => api.setHighlightPlayers(gameId, [...others, group]), 'Could not save player group');
    if (!ok) return;
    resetForm();
    setPlayerFilter(null);
    selectionCleared('Player group');
  };

  const removeGroup = async (g: RosterGroup) => {
    const ok = await mutate(`remove:${g.player_id}`, () => api.setHighlightPlayers(gameId, roster.filter((x) => x.player_id !== g.player_id)), 'Could not remove group');
    if (!ok) return;
    if (editingId === g.player_id) resetForm();
    setPlayerFilter(null);
    selectionCleared('Group removal');
  };

  // ----- render -----
  const status: HighlightStatus = bundle?.status ?? 'not_generated';
  const filterPlayer = playerFilter ? playerById.get(playerFilter) : undefined;

  return (
    <section className="panel highlights">
      <div className="panel-head">
        <h3>Highlight review</h3>
        <div className="hl-head-meta">
          {bundle && <span className="muted small">{bundle.scoring_version} · {bundle.score_kind}</span>}
          <span className={`status ${status}`}>{STATUS_LABEL[status]}</span>
        </div>
      </div>
      <p className="hl-disclaimer">
        Player stat lines count only reviewer-approved basketball events with a source time and a confirmed player — partial review, not full-game or official totals.
        Candidate “clip priority” (0–100) is an editorial heuristic for choosing footage; it does not measure basketball performance or ability.
      </p>

      <div className="hl-controls">
        <label className="select">
          Max clips
          <input type="number" min={MAX_CLIPS.min} max={MAX_CLIPS.max} value={maxClips} disabled={locked} onChange={(e) => setParam(setMaxClips, MAX_CLIPS)(e.target.value)} />
        </label>
        <label className="select">
          Top per player
          <input type="number" min={TOP_PER_PLAYER.min} max={TOP_PER_PLAYER.max} value={topPerPlayer} disabled={locked} onChange={(e) => setParam(setTopPerPlayer, TOP_PER_PLAYER)(e.target.value)} />
        </label>
        <button onClick={() => void generate()} disabled={locked || !jobId || load === 'loading'}>
          {busy === 'generate' ? 'Submitting…' : generating ? `Generating (${status})…` : 'Generate highlights'}
        </button>
        <span className="muted small">Reuses the latest successful tracking; no inference rerun.</span>
      </div>

      {analysisActive && <div className="alert warn">Analysis is running. Highlight actions are paused; highlights regenerate automatically once tracking finishes.</div>}
      {jobMismatch && (
        <div className="alert warn">
          These highlights belong to analysis <code>{bundle?.job_id?.slice(0, 8)}</code>, not the latest job <code>{jobId?.slice(0, 8)}</code>.
        </div>
      )}
      {loadError && (
        <div className="alert error">
          {loadError}
          <button className="link" onClick={() => void fetchBundle()}>Retry</button>
        </div>
      )}
      {bundle?.status === 'failed' && bundle.error && <div className="alert error">Highlight generation failed: {bundle.error}</div>}
      {actionError && <div className="alert error">{actionError}</div>}
      {notice && (
        <div className="alert warn">
          {notice}
          <button className="link" onClick={() => setNotice(null)}>Dismiss</button>
        </div>
      )}

      {!jobId ? (
        <p className="muted small hl-empty">Run analysis first; highlights are scored from completed tracking.</p>
      ) : load === 'loading' && !bundle ? (
        <p className="muted small hl-empty">Loading highlights…</p>
      ) : load === 'missing' ? (
        <p className="muted small hl-empty">No highlight data for this game yet. Once tracking succeeds, click <b>Generate highlights</b>.</p>
      ) : !bundle ? (
        <p className="muted small hl-empty">Highlights unavailable.</p>
      ) : (
        <>
          <div className="panel-head hl-section"><h4>Reviewed basketball evidence</h4><label className="toggle"><input type="checkbox" checked={showAllTracks} onChange={e => setShowAllTracks(e.target.checked)} /> Show all {players.length} track groups</label></div>
          {players.length === 0 ? (
            <p className="muted small">{status === 'not_generated' ? 'No highlight analysis yet. Click Generate highlights.' : 'No track groups with enough evidence.'}</p>
          ) : (
            <div className="hl-players">
              {players.filter(p => showAllTracks || p.candidate_count > 0 || p.identity_confirmed || !!p.basketball_stats?.has_evidence).map((p) => (
                <PlayerStatsCard
                  key={p.player_id}
                  player={p}
                  active={playerFilter === p.player_id}
                  onFilter={() => setPlayerFilter(playerFilter === p.player_id ? null : p.player_id)}
                  onGroup={p.identity_confirmed ? undefined : () => addTracksToForm(p.track_ids)}
                  canGroup={canMutate}
                  onSeek={onSeek}
                  candidateById={candidateById}
                />
              ))}
            </div>
          )}
          {!statsAvailable && players.length > 0 && (
            <p className="alert warn">Reviewed basketball stats are unavailable for this highlight data (older format). Stat lines show as unavailable; no rating is shown in their place.</p>
          )}
          <p className="fineprint">
            Automatic player IDs connect track fragments using uniform appearance and movement. They remain provisional; uncertain matches need review and can include non-players. Team A/B is an anonymous
            colour grouping and role flags are review prompts — neither is verified. Confirmed groups exist only because a reviewer grouped them. Observed duration counts sampled frames where the track was seen,
            not minutes played. Cards are ordered by amount of reviewed evidence, then confirmed groups, then observed duration.
          </p>

          <div className="panel-head hl-section">
            <h4>Candidates ({candidates.length}{filterPlayer ? ` · ${filterPlayer.label}` : ''})</h4>
            {filterPlayer && <button className="ghost small" onClick={() => setPlayerFilter(null)}>Show all</button>}
          </div>
          {candidates.length === 0 ? (
            <p className="muted small">No candidates{filterPlayer ? ' for this group' : ''}.</p>
          ) : (
            <ul className="hl-cands">
              {candidates.slice(0, candidateLimit).map((c) => (
                <CandidateRow
                  key={c.candidate_id}
                  candidate={c}
                  player={playerById.get(c.player_id)}
                  draft={draftFor(c)}
                  onDraft={(patch) => {
                    setDrafts((d) => ({ ...d, [c.candidate_id]: { ...(d[c.candidate_id] ?? initialDraft(c)), ...patch } }));
                    setRowError(c.candidate_id, null);
                  }}
                  onReview={(d) => void review(c, d)}
                  onSeek={ms => onSeek(ms, c.track_id)}
                  getPlayheadMs={getPlayheadMs}
                  nearby={nearbySameType}
                  heldReason={statusByCandidate.held.get(c.candidate_id)}
                  counted={statusByCandidate.counted.has(c.candidate_id)}
                  rowError={rowErrors[c.candidate_id]}
                  disabled={!canMutate}
                  saving={busy === `review:${c.candidate_id}`}
                  showClip={showClips}
                />
              ))}
            </ul>
          )}

          {candidates.length > candidateLimit && <button className="secondary small" onClick={() => setCandidateLimit(n => n + 6)}>Show more candidates ({candidates.length - candidateLimit} remaining)</button>}
          <div className="hl-mapping">
            <h4 className="hl-section">Uncertain fragment pairings</h4>
            <p className="muted small">Possible reconnections remain separate. Check both source fragments, then choose a pairing to load into the correction form below. Saving still requires your confirmation. Costs are matching heuristics, not probabilities.</p>
            {identityMergeSuggestions.filter(s => knownTracks.has(s.track_id)).length === 0 ? <p className="muted small">No pending reconnection suggestions for these tracks.</p> : <details>
              <summary>Review fragment suggestions ({identityMergeSuggestions.filter(s => knownTracks.has(s.track_id)).length})</summary>
              {identityMergeSuggestions.filter(s => knownTracks.has(s.track_id)).map(s => <article className="ball-candidate" key={`${s.track_id}:${s.timestamp_ms}`}>
                <strong>T{s.track_id} at {formatMs(s.timestamp_ms)}</strong>
                <button className="secondary small" onClick={() => onSeek(s.timestamp_ms, s.track_id)}>View fragment</button>
                {s.candidates.filter(c => c.track_ids.every(t => knownTracks.has(t))).map(c => <button key={c.player_id} className="ghost small" disabled={!canMutate} onClick={() => { addTracksToForm([s.track_id, ...c.track_ids]); setFormError(null); }}>Review pairing with {c.player_label} · T{c.track_ids.join(', T')} · gap {(c.gap_ms/1000).toFixed(1)} s · cost {c.score.toFixed(3)}</button>)}
                {!!s.excluded_candidates?.length && <span className="fineprint">{s.excluded_candidates.length} simultaneous alternatives excluded.</span>}
              </article>)}
            </details>}
            <h4 className="hl-section">Reviewer-confirmed groups</h4>
            <p className="muted small">
              Group track fragments only when you have checked the footage and they show the same person. Labels are private (e.g. <code>Jersey12</code>);
              nothing is inferred, published or linked to a profile. Groups reset when a new analysis runs.
            </p>
            {roster.length > 0 && (
              <ul className="hl-groups">
                {roster.map((g) => (
                  <li key={g.player_id}>
                    <strong>{g.label}</strong>
                    <span className="hl-tracks">
                      {g.track_ids.map((t) => (
                        <code key={t} style={{ ['--c' as string]: trackColor(String(t)) }}>T{t}</code>
                      ))}
                    </span>
                    <span className="hl-group-actions">
                      <button className="ghost small" onClick={() => editGroup(g)} disabled={!canMutate}>Edit</button>
                      <button className="ghost small danger" onClick={() => void removeGroup(g)} disabled={!canMutate}>
                        {busy === `remove:${g.player_id}` ? 'Removing…' : 'Remove'}
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <form className="hl-form" onSubmit={(e) => void submitGroup(e)}>
              <label>
                <span>Private label</span>
                <input value={groupLabel} onChange={(e) => setGroupLabel(e.target.value)} placeholder="Jersey12" maxLength={40} disabled={!canMutate} />
              </label>
              <label>
                <span>Current track IDs (comma-separated)</span>
                <input
                  value={groupTracks}
                  onChange={(e) => {
                    setGroupTracks(e.target.value);
                    setGroupConfirmed(false);
                  }}
                  placeholder="3, 17, 42"
                  disabled={!canMutate}
                />
              </label>
              <label className="check">
                <input type="checkbox" checked={groupConfirmed} onChange={(e) => setGroupConfirmed(e.target.checked)} disabled={!canMutate} />
                <span>I reviewed the footage and confirm these tracks show the same player.</span>
              </label>
              <div className="hl-form-actions">
                <button type="submit" className="secondary small" disabled={!canMutate || !groupConfirmed}>
                  {busy === 'players' ? 'Saving…' : editingId ? 'Update group' : 'Save group'}
                </button>
                {(editingId || groupLabel || groupTracks) && (
                  <button type="button" className="ghost small" onClick={resetForm}>Cancel</button>
                )}
              </div>
              {formError && <div className="alert error">{formError}</div>}
            </form>
          </div>
        </>
      )}
    </section>
  );
}

function parseTracks(text: string): string[] {
  const ids = text
    .split(/[\s,;]+/)
    .map((s) => s.trim().replace(/^T/i, ''))
    .filter(Boolean);
  return [...new Set(ids)];
}

const COMPONENT_CAP: Record<string, number> = {movement:35, raised_arm_pose:20, detection_quality:25, track_coverage:20};

function componentWidth(v: number, name: string): number | null {
  if (COMPONENT_CAP[name]) return Math.min(100, Math.max(0, v / COMPONENT_CAP[name] * 100));
  if (!Number.isFinite(v) || v < 0) return null;
  if (v <= 1) return v * 100;
  if (v <= 100) return v;
  return null;
}

function CandidateRow(props: {
  candidate: HighlightCandidate;
  player?: HighlightPlayer;
  draft: ReviewDraft;
  onDraft: (patch: Partial<ReviewDraft>) => void;
  onReview: (d: HighlightReviewStatus) => void;
  onSeek: (ms: number) => void;
  getPlayheadMs?: () => number;
  nearby: (c: HighlightCandidate, type: HighlightEventType, ms: number | null) => HighlightCandidate[];
  heldReason?: string;
  counted: boolean;
  rowError?: string;
  disabled: boolean;
  saving: boolean;
  showClip: boolean;
}) {
  const { candidate: c, player, draft, disabled, saving } = props;
  const outcome = outcomeLabel(c);
  const rejected = c.review_status === 'rejected';
  const adjusted = Math.abs(c.score - c.automatic_score) >= 0.05;
  const components = Object.entries(c.components ?? {});
  const saved = savedReview(c);
  const savedTime = savedEventTimeMs(c);
  const outcomeDraft = isBasketballOutcome(draft.event_type);
  const draftMs = parseDraftTime(draft);
  const liveBoundsError = outcomeDraft && draftMs !== null ? timeBoundsError(c, draftMs) : null;
  const neighbours = outcomeDraft ? props.nearby(c, draft.event_type, draftMs) : [];
  const id = c.candidate_id;

  return (
    <li className={`hl-cand ${c.review_status}${c.selected ? ' selected' : ''}`}>
      <div className="hl-cand-score" title="Editorial clip priority 0–100 for choosing footage — not basketball performance">
        {rejected ? '—' : Math.round(c.score)}
        <small>{rejected ? 'excluded' : 'clip priority'}</small>
      </div>
      <div className="hl-cand-main">
        <div className="hl-cand-head">
          <strong>{player?.label ?? c.player_id}</strong>
          <code style={{ ['--c' as string]: trackColor(String(c.track_id)) }}>T{c.track_id}</code>
          {player && <span className={`hl-badge ${player.identity_confirmed ? 'ok' : ''}`}>{player.identity_confirmed ? 'confirmed group' : 'provisional'}</span>}
          <span className={`review ${c.review_status}`}>{c.review_status}</span>
          {c.selected && <span className="hl-badge sel">selected for clip</span>}
          {props.counted && <span className="hl-badge ok">counted in reviewed stats</span>}
          {props.heldReason && <span className="hl-badge held">held from stats</span>}
        </div>
        <p className={`hl-outcome ${outcome.confirmed ? 'confirmed' : ''}`}>{outcome.text}</p>
        {saved && c.review_status === 'approved' && isBasketballOutcome(saved.event_type ?? c.event_type) && (
          <p className="hl-saved small">
            Saved: event at {savedTime !== null ? formatMs(savedTime) : '— (no time)'}
            {(saved.event_type ?? c.event_type) === 'made_shot' && <> · value {saved.points ?? 'unknown'}</>}
            {' · '}actor {saved.actor_confirmed ? 'confirmed' : 'not confirmed'}
            {saved.distinct_event_confirmed ? ' · confirmed distinct' : ''}
          </p>
        )}
        {props.heldReason && <p className="hl-held small">Held from totals: {heldReasonText(props.heldReason)}</p>}
        <p className="range">
          source {formatMs(c.start_ms)} → {formatMs(c.end_ms)} <span className="muted">· action {formatMs(c.action_start_ms)} → {formatMs(c.action_end_ms)}</span>
        </p>
        {adjusted && (
          <p className="fineprint">
            Automatic clip priority {c.automatic_score.toFixed(1)} → {c.score.toFixed(1)} after reviewer decision (editorial weight, not a stat).
          </p>
        )}
        {components.length > 0 && (
          <details className="hl-why">
            <summary>Why this clip was suggested (visual heuristic)</summary>
            <dl className="hl-components">
              {components.map(([k, v]) => {
                const w = componentWidth(v, k);
                return (
                  <div key={k}>
                    <dt>{k.replace(/_/g, ' ')}{COMPONENT_CAP[k] ? ` / ${COMPONENT_CAP[k]}` : ''}</dt>
                    <dd>
                      {w !== null && <i style={{ width: `${w}%` }} />}
                      <span>{Number.isFinite(v) ? v.toFixed(2) : '–'}</span>
                    </dd>
                  </div>
                );
              })}
            </dl>
            {c.reasons.length > 0 && (
              <ul className="hl-reasons">
                {c.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            )}
          </details>
        )}
        {props.showClip && c.selected && c.clip_uri ? (
          <div className="hl-preview">
            <video src={c.clip_uri} controls preload="metadata" playsInline />
            <span className="muted small">Yellow marker follows the selected track briefly around the action.</span>
            {c.qa_status && <span className={`qa ${c.qa_status === 'passed' ? 'ok' : 'bad'}`}>QA {c.qa_status}</span>}
          </div>
        ) : c.selected ? (
          <p className="fineprint">Selected — no current rendered clip. Click Generate highlights to render it.</p>
        ) : null}

        <div className="hl-review">
          <div className="hl-cand-actions">
            <button className="secondary small" onClick={() => props.onSeek(savedTime ?? c.action_start_ms)}>Seek source</button>
            <label className="select" title="Outcome type is applied only when approving">
              Outcome if approved
              <select value={draft.event_type} onChange={(e) => props.onDraft({ event_type: e.target.value as HighlightEventType })} disabled={disabled}>
                {EVENT_TYPES.map((t) => (
                  <option key={t} value={t}>{EVENT_TYPE_LABEL[t]}</option>
                ))}
              </select>
            </label>
          </div>
          {outcomeDraft && (
            <div className="hl-facts">
              <label className="select">
                Event time (s)
                <input
                  type="number"
                  inputMode="decimal"
                  step={0.01}
                  min={(c.start_ms / 1000).toFixed(2)}
                  value={draft.timeSec}
                  onChange={(e) => props.onDraft({ timeSec: e.target.value })}
                  disabled={disabled}
                  aria-invalid={!!liveBoundsError}
                  aria-describedby={liveBoundsError ? `t-${id}` : undefined}
                />
              </label>
              {props.getPlayheadMs && (
                <button type="button" className="ghost small" disabled={disabled} onClick={() => props.onDraft({ timeSec: (props.getPlayheadMs!() / 1000).toFixed(2) })}>
                  Use player time
                </button>
              )}
              <button type="button" className="ghost small" onClick={() => draftMs !== null && props.onSeek(draftMs)} disabled={draftMs === null}>
                Check time
              </button>
              {draft.event_type === 'made_shot' && (
                <label className="select">
                  Shot value
                  <select value={draft.points} onChange={(e) => props.onDraft({ points: e.target.value as PointsChoice })} disabled={disabled}>
                    <option value="">Choose…</option>
                    <option value="1">1 pt</option>
                    <option value="2">2 pts</option>
                    <option value="3">3 pts</option>
                    <option value="unknown">Unknown (no points total)</option>
                  </select>
                </label>
              )}
              {draft.event_type === 'made_free_throw' && <span className="muted small">Free throw: 1 point</span>}
              <label className="check">
                <input type="checkbox" checked={draft.actorConfirmed} onChange={(e) => props.onDraft({ actorConfirmed: e.target.checked })} disabled={disabled} />
                <span>I confirmed this player performed the play{player?.identity_confirmed ? ' (reviewer-confirmed group also counts)' : ''}.</span>
              </label>
              {(neighbours.length > 0 || draft.distinctConfirmed) && (
                <label className="check">
                  <input type="checkbox" checked={draft.distinctConfirmed} onChange={(e) => props.onDraft({ distinctConfirmed: e.target.checked })} disabled={disabled} />
                  <span>
                    This is a different {eventTypeLabel(draft.event_type).toLowerCase()} from{' '}
                    {neighbours.length ? neighbours.map((n) => `T${n.track_id} @ ${formatMs(savedEventTimeMs(n) ?? n.action_start_ms)}`).join(', ') : 'nearby events'} (within 1.5 s).
                    Unconfirmed near-duplicates are held from totals.
                  </span>
                </label>
              )}
              {liveBoundsError && <p id={`t-${id}`} className="hl-held small">{liveBoundsError}</p>}
              {!draft.actorConfirmed && !player?.identity_confirmed && (
                <p className="muted small">Without actor confirmation the approval saves but stays held from stats.</p>
              )}
            </div>
          )}
          <div className="hl-cand-actions">
            <button className="small" onClick={() => props.onReview('approved')} disabled={disabled}>
              {saving ? 'Saving…' : c.review_status === 'approved' ? 'Update approval' : 'Approve'}
            </button>
            <button className="ghost small danger" onClick={() => props.onReview('rejected')} disabled={disabled || rejected}>Reject</button>
            <button className="ghost small" onClick={() => props.onReview('pending')} disabled={disabled || c.review_status === 'pending'}>Reset</button>
          </div>
          {props.rowError && <div className="alert error">{props.rowError}</div>}
        </div>
      </div>
    </li>
  );
}
