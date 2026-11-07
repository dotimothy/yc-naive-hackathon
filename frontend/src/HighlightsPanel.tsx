import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { api, ApiError } from './api';
import { formatMs, trackColor } from './FilmPlayer';
import type {
  HighlightBundle,
  HighlightCandidate,
  HighlightEventType,
  HighlightPlayer,
  HighlightReviewStatus,
  HighlightStatus,
  RosterGroup,
} from './types';

// Contract: docs/HIGHLIGHTS_CONTRACT.md. Scores are highlight *priority* for footage review —
// visual activity / footage quality heuristics — never talent ratings, verified events or rankings.

export type HighlightsPanelProps = {
  gameId: string;
  jobId: string | null;
  analysisActive: boolean;
  onSeek: (ms: number, trackId?: string) => void;
  onUpdated: () => void;
};

const POLL_MS = 900;
const ACTIVE_STATUS = new Set<HighlightStatus>(['queued', 'running']);
const MAX_CLIPS = { min: 1, max: 12, initial: 8 };
const TOP_PER_PLAYER = { min: 1, max: 3, initial: 2 };

export const EVENT_TYPE_LABEL: Record<HighlightEventType, string> = {
  visual_activity: 'Visual activity (no outcome)',
  made_shot: 'Made shot',
  assist: 'Assist',
  rebound: 'Rebound',
  steal: 'Steal',
  block: 'Block',
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
const seconds = (ms: number) => `${(Math.max(0, ms) / 1000).toFixed(1)} s`;

function newKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Human-readable outcome label that never presents a heuristic pick as a confirmed play. */
export function outcomeLabel(c: Pick<HighlightCandidate, 'origin' | 'event_type' | 'review_status'>): { text: string; confirmed: boolean } {
  if (c.origin === 'reviewer_confirmed' && c.event_type !== 'visual_activity') {
    return { text: `Reviewer-confirmed: ${EVENT_TYPE_LABEL[c.event_type] ?? c.event_type}`, confirmed: true };
  }
  if (c.review_status === 'approved') return { text: 'Reviewer-approved visual activity · no outcome claimed', confirmed: false };
  return { text: 'Provisional visual activity · heuristic, unverified', confirmed: false };
}

/** Reset every piece of state when the analysis job changes by remounting. */
export function HighlightsPanel(props: HighlightsPanelProps) {
  return <HighlightsPanelInner key={`${props.gameId}:${props.jobId ?? 'none'}`} {...props} />;
}

type LoadState = 'loading' | 'ready' | 'missing' | 'error';

function HighlightsPanelInner({ gameId, jobId, analysisActive, onSeek, onUpdated }: HighlightsPanelProps) {
  const [bundle, setBundle] = useState<HighlightBundle | null>(null);
  const [load, setLoad] = useState<LoadState>('loading');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pollTick, setPollTick] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [maxClips, setMaxClips] = useState(MAX_CLIPS.initial);
  const [topPerPlayer, setTopPerPlayer] = useState(TOP_PER_PLAYER.initial);
  const [drafts, setDrafts] = useState<Record<string, HighlightEventType>>({});
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
  }, [analysisActive, fetchBundle]);

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

  const review = async (c: HighlightCandidate, decision: HighlightReviewStatus) => {
    const event_type: HighlightEventType = decision === 'approved' ? drafts[c.candidate_id] ?? c.event_type ?? 'visual_activity' : 'visual_activity';
    const ok = await mutate(`review:${c.candidate_id}`, () => api.reviewHighlight(gameId, c.candidate_id, { decision, event_type }), 'Could not save review');
    if (!ok) return;
    if (decision !== 'approved') setDrafts(({ [c.candidate_id]: _drop, ...rest }) => rest);
    selectionCleared('Review');
  };

  // ----- derived data -----
  const players = useMemo(() => {
    const list = bundle?.players ?? [];
    // Ascending highlight priority; unscored tracks follow scored tracks.
    return [...list].sort((a, b) => (a.highlight_score ?? Infinity) - (b.highlight_score ?? Infinity) || a.label.localeCompare(b.label, undefined, { numeric: true }));
  }, [bundle]);
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
        Highlight priority (0–100) selects footage using visible activity and tracking quality. Reviewers can confirm basketball outcomes.
        These experimental scores measure highlight priority; player ability is not measured.
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
          <div className="panel-head hl-section"><h4>Player / track highlights</h4><label className="toggle"><input type="checkbox" checked={showAllTracks} onChange={e => setShowAllTracks(e.target.checked)} /> Show all {players.length} track groups</label></div>
          {players.length === 0 ? (
            <p className="muted small">{status === 'not_generated' ? 'No highlight analysis yet. Click Generate highlights.' : 'No track groups with enough evidence.'}</p>
          ) : (
            <div className="hl-players">
              {players.filter(p => showAllTracks || p.candidate_count > 0 || p.identity_confirmed).map((p) => (
                <PlayerCard
                  key={p.player_id}
                  player={p}
                  active={playerFilter === p.player_id}
                  onFilter={() => setPlayerFilter(playerFilter === p.player_id ? null : p.player_id)}
                  onGroup={p.identity_confirmed ? undefined : () => addTracksToForm(p.track_ids)}
                  canGroup={canMutate}
                />
              ))}
            </div>
          )}
          <p className="fineprint">
            Automatic player IDs connect track fragments using uniform appearance and movement. They remain provisional; uncertain matches need review and can include non-players. Confirmed groups exist only
            because a reviewer grouped them. Observed duration counts sampled frames where the track was seen, not minutes played. Cards are ordered by highlight priority, lowest to highest; unscored tracks appear last.
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
                  draft={drafts[c.candidate_id] ?? c.event_type}
                  onDraft={(t) => setDrafts((d) => ({ ...d, [c.candidate_id]: t }))}
                  onReview={(d) => void review(c, d)}
                  onSeek={ms => onSeek(ms, c.track_id)}
                  disabled={!canMutate}
                  saving={busy === `review:${c.candidate_id}`}
                  showClip={showClips}
                />
              ))}
            </ul>
          )}

          {candidates.length > candidateLimit && <button className="secondary small" onClick={() => setCandidateLimit(n => n + 6)}>Show more candidates ({candidates.length - candidateLimit} remaining)</button>}
          <div className="hl-mapping">
            <h4 className="hl-section">Reviewer-confirmed groups</h4>
            <p className="muted small">
              Group track fragments only when you have checked the footage and they show the same person. Labels are private and local (e.g. <code>Jersey12</code>);
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

function PlayerCard({ player: p, active, onFilter, onGroup, canGroup }: { player: HighlightPlayer; active: boolean; onFilter: () => void; onGroup?: () => void; canGroup: boolean }) {
  return (
    <article className={`hl-player ${p.identity_confirmed ? 'confirmed' : 'provisional'}${active ? ' on' : ''}`}>
      <header>
        <strong title={p.player_id}>{p.label}</strong>
        <span className={`hl-badge ${p.identity_confirmed ? 'ok' : ''}`}>{p.identity_confirmed ? 'Confirmed' : p.association_status === 'needs_review' ? 'Association needs review' : p.identity_source === 'automatic' ? 'Automatic player ID' : 'Provisional'}</span>
      </header>
      <div className="hl-score">
        {p.highlight_score !== null && Number.isFinite(p.highlight_score) ? (
          <>
            <b>{Math.round(p.highlight_score)}</b>
            <small>/100 highlight priority</small>
          </>
        ) : (
          <span className="muted small">No score — insufficient or rejected-only evidence</span>
        )}
      </div>
      <div className="hl-tracks">
        {p.track_ids.map((t) => (
          <code key={t} style={{ ['--c' as string]: trackColor(String(t)) }}>T{t}</code>
        ))}
      </div>
      <dl className="metrics">
        <div><dt>Observed sample duration</dt><dd>{seconds(p.observed_ms)}</dd></div>
        <div><dt>Reviewer-verified plays</dt><dd>{p.verified_play_count}</dd></div>
        <div><dt>Candidates</dt><dd>{p.candidate_count}</dd></div>
        <div><dt>Selected for render</dt><dd>{p.selected_count}</dd></div>
      </dl>
      <div className="hl-card-actions">
        <button className="secondary small" onClick={onFilter}>{active ? 'Show all candidates' : 'Show candidates'}</button>
        {onGroup && (
          <button className="ghost small" onClick={onGroup} disabled={!canGroup} title="Add these track IDs to the group form">
            Add to group form
          </button>
        )}
      </div>
    </article>
  );
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
  draft: HighlightEventType;
  onDraft: (t: HighlightEventType) => void;
  onReview: (d: HighlightReviewStatus) => void;
  onSeek: (ms: number) => void;
  disabled: boolean;
  saving: boolean;
  showClip: boolean;
}) {
  const { candidate: c, player, draft, disabled, saving } = props;
  const outcome = outcomeLabel(c);
  const rejected = c.review_status === 'rejected';
  const adjusted = Math.abs(c.score - c.automatic_score) >= 0.05;
  const components = Object.entries(c.components ?? {});

  return (
    <li className={`hl-cand ${c.review_status}${c.selected ? ' selected' : ''}`}>
      <div className="hl-cand-score" title="Highlight priority 0–100 (not a talent rating)">
        {rejected ? '—' : Math.round(c.score)}
        <small>{rejected ? 'excluded' : 'priority'}</small>
      </div>
      <div className="hl-cand-main">
        <div className="hl-cand-head">
          <strong>{player?.label ?? c.player_id}</strong>
          <code style={{ ['--c' as string]: trackColor(String(c.track_id)) }}>T{c.track_id}</code>
          {player && <span className={`hl-badge ${player.identity_confirmed ? 'ok' : ''}`}>{player.identity_confirmed ? 'confirmed group' : 'provisional'}</span>}
          <span className={`review ${c.review_status}`}>{c.review_status}</span>
          {c.selected && <span className="hl-badge sel">selected</span>}
        </div>
        <p className={`hl-outcome ${outcome.confirmed ? 'confirmed' : ''}`}>{outcome.text}</p>
        <p className="range">
          source {formatMs(c.start_ms)} → {formatMs(c.end_ms)} <span className="muted">· action {formatMs(c.action_start_ms)} → {formatMs(c.action_end_ms)}</span>
        </p>
        {adjusted && (
          <p className="fineprint">
            Automatic visual score {c.automatic_score.toFixed(1)} → {c.score.toFixed(1)} after reviewer decision (transparent heuristic weight, not an official stat).
          </p>
        )}
        {components.length > 0 && (
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
        )}
        {c.reasons.length > 0 && (
          <ul className="hl-reasons">
            {c.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        )}
        {props.showClip && c.selected && c.clip_uri ? (
          <div className="hl-preview">
            <video src={c.clip_uri} controls preload="auto" playsInline />
            <span className="muted small">Yellow marker follows the selected track briefly around the action.</span>
            {c.qa_status && <span className={`qa ${c.qa_status === 'passed' ? 'ok' : 'bad'}`}>QA {c.qa_status}</span>}
          </div>
        ) : c.selected ? (
          <p className="fineprint">Selected — no current rendered clip. Click Generate highlights to render it.</p>
        ) : null}
        <div className="hl-cand-actions">
          <button className="secondary small" onClick={() => props.onSeek(c.action_start_ms)}>Seek source</button>
          <label className="select" title="Outcome type is applied only when approving">
            Outcome if approved
            <select value={draft} onChange={(e) => props.onDraft(e.target.value as HighlightEventType)} disabled={disabled}>
              {EVENT_TYPES.map((t) => (
                <option key={t} value={t}>{EVENT_TYPE_LABEL[t]}</option>
              ))}
            </select>
          </label>
          <button className="small" onClick={() => props.onReview('approved')} disabled={disabled}>
            {saving ? 'Saving…' : c.review_status === 'approved' ? 'Update approval' : 'Approve'}
          </button>
          <button className="ghost small danger" onClick={() => props.onReview('rejected')} disabled={disabled || rejected}>Reject</button>
          <button className="ghost small" onClick={() => props.onReview('pending')} disabled={disabled || c.review_status === 'pending'}>Reset</button>
        </div>
      </div>
    </li>
  );
}
