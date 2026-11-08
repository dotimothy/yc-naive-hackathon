import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from './api';
import { formatMs, trackColor } from './FilmPlayer';
import type { Clip, Game, HighlightBundle, Job, TrackSample } from './types';
import type { DelayStats, StreamState } from './useGameRoom';

// Identity fields are optional: older runs only carry temporary track IDs.
type Sample = TrackSample & { player_id?: string; player_label?: string };

type Leader = {
  key: string;
  label: string;
  trackId: string;
  firstMs: number;
  value: number;
  max: number;
  valueText: string;
  detail: string;
  confirmed: boolean;
};

type Mode = 'priority' | 'screen';

type GameState = { key: string; label: string };

function gameState(job: Job | null): GameState {
  if (!job) return { key: 'pregame', label: 'Pregame' };
  if (job.status === 'running') return { key: 'live', label: 'Analyzing' };
  if (job.status === 'queued') return { key: 'queued', label: 'Warming Up' };
  if (job.status === 'failed') return { key: 'failed', label: 'Failed' };
  return { key: 'final', label: 'Analyzed' };
}

const seconds = (ms: number) => (ms >= 60000 ? formatMs(ms) : `${(ms / 1000).toFixed(1)}s`);

export function LiveBoard(props: {
  refreshKey?: number;
  game: Game;
  job: Job | null;
  tracks: Map<string, TrackSample>;
  clips: Clip[];
  progress: { processed_frames?: number; source_timestamp_ms: number } | null;
  streamState: StreamState;
  delay: DelayStats;
  actions: ReactNode;
  onPick: (trackId: string, ms: number) => void;
}) {
  const { game, job, tracks, clips, progress } = props;
  const state = gameState(job);
  const bundle = useHighlightBundle(game.game_id, job, clips.length, props.refreshKey);

  // Live per-player tallies from streamed track samples.
  const onScreen = useMemo(() => {
    const groups = new Map<string, { label: string; trackId: string; count: number; first: number; last: number }>();
    for (const raw of tracks.values()) {
      const s = raw as Sample;
      const key = s.player_id ?? s.track_id;
      const g = groups.get(key);
      if (g) {
        g.count++;
        if (s.timestamp_ms < g.first) {
          g.first = s.timestamp_ms;
          g.trackId = s.track_id;
        }
        g.last = Math.max(g.last, s.timestamp_ms);
      } else {
        groups.set(key, { label: s.player_label ?? `Track ${s.track_id}`, trackId: s.track_id, count: 1, first: s.timestamp_ms, last: s.timestamp_ms });
      }
    }
    return groups;
  }, [tracks]);

  const fps = game.fps || 30;
  const stride = job?.report?.stride ?? 5;
  const sampleMs = (stride / fps) * 1000;

  const priorityLeaders: Leader[] = useMemo(() => {
    const scored = (bundle?.players ?? []).filter((p) => !!p.basketball_stats?.has_evidence);
    return scored
      .sort((a, b) => (b.basketball_stats?.reviewed_events_count ?? 0) - (a.basketball_stats?.reviewed_events_count ?? 0))
      .slice(0, 5)
      .map((p) => {
        const trackId = p.track_ids[0] ?? p.player_id;
        const first = p.track_ids.map((t) => onScreen.get(t)?.first).find((v) => v !== undefined) ?? 0;
        return {
          key: p.player_id,
          label: p.label,
          trackId,
          firstMs: first,
          value: p.basketball_stats?.reviewed_events_count ?? 0,
          max: Math.max(1, ...scored.map(p => p.basketball_stats?.reviewed_events_count ?? 0)),
          valueText: String(p.basketball_stats?.reviewed_events_count ?? 0),
          detail: `${p.basketball_stats?.points ?? "?"} PTS · ${p.basketball_stats?.field_goals_made ?? 0}/${p.basketball_stats?.field_goals_attempted ?? 0} FG · reviewed evidence only`,
          confirmed: p.identity_confirmed,
        };
      });
  }, [bundle, onScreen]);

  const screenLeaders: Leader[] = useMemo(() => {
    const list = [...onScreen.entries()].sort((a, b) => b[1].count - a[1].count).slice(0, 5);
    const top = list[0]?.[1].count ?? 1;
    return list.map(([key, g]) => ({
      key,
      label: g.label,
      trackId: g.trackId,
      firstMs: g.first,
      value: g.count,
      max: top,
      valueText: seconds(g.count * sampleMs),
      detail: `${g.count} Samples · ${formatMs(g.first)}–${formatMs(g.last)}`,
      confirmed: false,
    }));
  }, [onScreen, sampleMs]);

  const [mode, setMode] = useState<Mode | null>(null);
  const activeMode: Mode = mode ?? (priorityLeaders.length ? 'priority' : 'screen');
  const leaders = activeMode === 'priority' ? priorityLeaders : screenLeaders;

  const pct =
    job?.status === 'succeeded'
      ? 100
      : progress
      ? Math.min(100, (progress.source_timestamp_ms / Math.max(1, game.duration_ms)) * 100)
      : 0;
  const passed = clips.filter((c) => c.qa_status === 'passed').length;
  const report = job?.report;

  return (
    <section className={`board state-${state.key}`} aria-label="Live game board">
      <div className="board-top">
        <div className="board-game">
          <div className="board-tags">
            <span className={`game-state ${state.key}`}><i />{state.label}</span>
            <span className="board-sport">{game.sport}</span>
            <span className={`stream ${props.streamState}`} title="Live update stream from the local API"><i />{props.streamState === 'live' ? 'Feed Live' : props.streamState === 'connecting' ? 'Connecting' : props.streamState === 'reconnecting' ? 'Reconnecting' : 'Feed Closed'}</span>
          </div>
          <h2>{game.title}</h2>
          <p className="board-meta">
            {game.width}×{game.height} · {Number(game.fps).toFixed(2)} fps · {formatMs(game.duration_ms)}
            {game.codec ? ` · ${game.codec}` : ''} · <code title={game.game_id}>{game.game_id.slice(0, 8)}</code>
          </p>
        </div>
        <div className="board-actions">{props.actions}</div>
      </div>

      <dl className="board-stats">
        <Stat label="Provisional Groups" value={onScreen.size || '–'} hint={onScreen.size ? 'distinct player / track IDs' : 'awaiting tracks'} />
        <Stat label="Detections" value={tracks.size ? tracks.size.toLocaleString() : '–'} hint="pose samples received" />
        <Stat label="Clips" value={clips.length ? `${passed}/${clips.length}` : '–'} hint="passed QA / rendered" />
        <Stat label="Player Inference FPS" value={report ? report.inference_fps.toFixed(1) : '–'} hint={report ? `${report.execution_mode ?? report.device ?? 'cpu'} · measured` : 'after first run'} />
        <Stat label="Feed Delay" value={props.delay ? `${props.delay.median}` : '–'} unit={props.delay ? 'ms' : undefined} hint="median, server → browser" />
      </dl>

      <div className="quarters" aria-label={`Film processed ${Math.round(pct)}%`}>
        {['0–25%', '25–50%', '50–75%', '75–100%'].map((q, i) => {
          const fill = Math.min(100, Math.max(0, (pct - i * 25) * 4));
          return (
            <div key={q} className="quarter">
              <div className="quarter-bar"><i style={{ width: `${fill}%` }} /></div>
              <span>{q}</span>
            </div>
          );
        })}
        <b>{Math.round(pct)}%<small> Film Processed</small></b>
      </div>

      <div className="leaders">
        <div className="leaders-head">
          <h3>Film Review Queue</h3>
          <div className="seg-toggle" role="tablist">
            <button role="tab" aria-selected={activeMode === 'priority'} className={activeMode === 'priority' ? 'on' : ''} onClick={() => setMode('priority')} disabled={!priorityLeaders.length}>
              Reviewed Events
            </button>
            <button role="tab" aria-selected={activeMode === 'screen'} className={activeMode === 'screen' ? 'on' : ''} onClick={() => setMode('screen')} disabled={!screenLeaders.length}>
              Screen Time
            </button>
          </div>
        </div>
        {leaders.length === 0 ? (
          <p className="leaders-empty">
            {!job
              ? 'Run analysis to fill the board. Players appear here live as tracking streams in.'
              : job.status === 'failed'
              ? 'No tracking data: the analysis failed.'
              : 'Waiting for the first tracked frames…'}
          </p>
        ) : (
          <ol className="leader-list">
            {leaders.map((l, i) => (
              <li key={l.key}>
                <button className="leader" onClick={() => props.onPick(l.trackId, l.firstMs)} title="Isolate on film and seek to first appearance" style={{ ['--c' as string]: trackColor(l.key) }}>
                  <span className="leader-rank">{i + 1}</span>
                  <span className="leader-id">
                    <strong>{l.label}</strong>
                    <small>{l.detail}</small>
                  </span>
                  <span className="leader-bar"><i style={{ width: `${(l.value / Math.max(1, l.max)) * 100}%` }} /></span>
                  <span className="leader-value">{l.valueText}</span>
                  {l.confirmed && <span className="hl-badge ok">Confirmed</span>}
                </button>
              </li>
            ))}
          </ol>
        )}
        <p className="fineprint">
          {activeMode === 'priority'
            ? 'Counts cover reviewed source events only, ordered by evidence available. These are partial samples, not a player performance ranking.'
            : 'Screen time = sampled frames where the player/track was detected × sampling interval. Not minutes played; tracks can include referees or spectators.'}{' '}
          Labels are provisional unless confirmed by a reviewer. Click a row to isolate it on film.
        </p>
      </div>
    </section>
  );
}

function Stat({ label, value, unit, hint }: { label: string; value: ReactNode; unit?: string; hint: string }) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd>
        {value}
        {unit && <small>{unit}</small>}
      </dd>
      <span>{hint}</span>
    </div>
  );
}

/** Lightweight read-only view of the highlight bundle for the board; refetches as the job/clips change. */
function useHighlightBundle(gameId: string, job: Job | null, clipCount: number, refreshKey?: number): HighlightBundle | null {
  const [bundle, setBundle] = useState<HighlightBundle | null>(null);
  const [tick, setTick] = useState(0);
  const jobKey = `${job?.job_id ?? ''}:${job?.status ?? ''}:${job?.attempts ?? 0}`;

  useEffect(() => {
    const controller = new AbortController();
    api
      .highlights(gameId, controller.signal)
      .then((b) => setBundle(b))
      .catch(() => undefined);
    return () => controller.abort();
  }, [gameId, jobKey, clipCount, tick, refreshKey]);

  const pending = bundle?.status === 'queued' || bundle?.status === 'running';
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => setTick((t) => t + 1), 3000);
    return () => window.clearInterval(timer);
  }, [pending]);

  return bundle;
}
