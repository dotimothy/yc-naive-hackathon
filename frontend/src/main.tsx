import { StrictMode, useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { api, ApiError } from './api';
import { buildFrameIndex, FilmPlayer, formatMs, Timeline, trackColor, usePlayhead } from './FilmPlayer';
import { EVENT_TYPE_LABEL, HighlightsPanel } from './HighlightsPanel';
import type { Clip, Game, GameEvent, Job } from './types';
import { isTerminal, useGameRoom, type DelayStats, type StreamState } from './useGameRoom';
import './styles.css';

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

function App() {
  const [games, setGames] = useState<Game[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const loadGames = useCallback(async () => {
    try {
      const items = await api.listGames();
      setGames(items);
      setListError(null);
      setSelectedId((current) => current ?? items[items.length - 1]?.game_id ?? null);
    } catch (e) {
      setListError(errorText(e, 'Could not load games'));
      setGames((g) => g ?? []);
    }
  }, []);

  useEffect(() => {
    void loadGames();
  }, [loadGames]);

  const addGame = (game: Game) => {
    setGames((g) => [...(g ?? []).filter((x) => x.game_id !== game.game_id), game]);
    setSelectedId(game.game_id);
  };

  const selected = games?.find((g) => g.game_id === selectedId) ?? null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true">◉</span>
          <div>
            <h1>ScoutFilm</h1>
            <p>Local Film Room</p>
          </div>
        </div>
        <div className="topbar-meta">
          <span className="pill">Basketball · prototype</span>
          <span className="pill muted">Loopback only · no uploads leave this machine</span>
        </div>
      </header>
      <div className="layout">
        <aside className="sidebar">
          <IngestPanel onGame={addGame} />
          <section className="panel">
            <div className="panel-head">
              <h2>Games</h2>
              <button className="ghost small" onClick={() => void loadGames()}>Refresh</button>
            </div>
            {listError && (
              <div className="alert error">
                {listError}
                <button className="link" onClick={() => void loadGames()}>Retry</button>
              </div>
            )}
            {games === null ? (
              <p className="muted small">Loading games…</p>
            ) : games.length === 0 ? (
              <p className="muted small">No games yet. Upload an authorized MP4 or load the sample.</p>
            ) : (
              <ul className="game-list">
                {[...games].reverse().map((g) => (
                  <li key={g.game_id}>
                    <button className={g.game_id === selectedId ? 'game active' : 'game'} onClick={() => setSelectedId(g.game_id)}>
                      <strong>{g.title}</strong>
                      <span>
                        {g.sport} · {formatMs(g.duration_ms)} · {g.width}×{g.height} · {Number(g.fps).toFixed(2)} fps
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
        <main className="main">
          {selected ? (
            <GameRoom key={selected.game_id} game={selected} />
          ) : (
            <div className="empty">
              <h2>No game selected</h2>
              <p>Upload basketball footage you are authorized to use, or load the TrackID3x3 sample, then run analysis to see real model detections and pose.</p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function IngestPanel({ onGame }: { onGame: (g: Game) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [rights, setRights] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState<'upload' | 'sample' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file || !rights) return;
    setBusy('upload');
    setError(null);
    setProgress(0);
    try {
      const game = await api.uploadGame(file, rights, setProgress);
      onGame(game);
      setFile(null);
      setRights(false);
      if (inputRef.current) inputRef.current.value = '';
    } catch (err) {
      setError(errorText(err, 'Upload failed'));
    } finally {
      setBusy(null);
      setProgress(null);
    }
  };

  const sample = async () => {
    setBusy('sample');
    setError(null);
    try {
      onGame(await api.loadSample());
    } catch (err) {
      const msg = errorText(err, 'Could not load sample');
      setError(err instanceof ApiError && err.status === 404 ? `${msg} (python scripts/download_sample.py)` : msg);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel">
      <h2>Add footage</h2>
      <form onSubmit={submit} className="ingest">
        <label className="file">
          <input ref={inputRef} type="file" accept="video/mp4,.mp4" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <span>{file ? `${file.name} · ${(file.size / 1048576).toFixed(1)} MiB` : 'Choose MP4 (≤ 250 MiB)'}</span>
        </label>
        <label className="check">
          <input type="checkbox" checked={rights} onChange={(e) => setRights(e.target.checked)} />
          <span>I own this footage or am authorized to analyze it locally.</span>
        </label>
        <button type="submit" disabled={!file || !rights || busy !== null}>
          {busy === 'upload' ? (progress !== null && progress < 1 ? `Uploading ${Math.round(progress * 100)}%` : 'Probing video…') : 'Upload game'}
        </button>
        {file && !rights && <p className="muted small">Confirm footage rights to enable upload.</p>}
      </form>
      <div className="divider"><span>or</span></div>
      <button className="secondary" onClick={() => void sample()} disabled={busy !== null}>
        {busy === 'sample' ? 'Loading sample…' : 'Load TrackID3x3 sample'}
      </button>
      {error && <div className="alert error">{error}</div>}
    </section>
  );
}

const STREAM_LABEL: Record<StreamState, string> = {
  connecting: 'Connecting',
  live: 'Live',
  reconnecting: 'Reconnecting…',
  closed: 'Stream closed',
};

function GameRoom({ game }: { game: Game }) {
  const room = useGameRoom(game.game_id);
  const videoRef = useRef<HTMLVideoElement>(null);
  const playheadMs = usePlayhead(videoRef);
  const [showBoxes, setShowBoxes] = useState(true);
  const [showPose, setShowPose] = useState(true);
  const [selectedTrack, setSelectedTrack] = useState<string | null>(null);

  const index = useMemo(() => buildFrameIndex(room.tracks), [room.tracks]);
  const clips = useMemo(() => Object.values(room.clips).sort((a, b) => a.source_start_ms - b.source_start_ms), [room.clips]);
  const events = room.events;

  // Drop a track selection that no longer exists (e.g. new job).
  useEffect(() => {
    if (selectedTrack && !index.trackIds.some((t) => t.id === selectedTrack)) setSelectedTrack(null);
  }, [index, selectedTrack]);

  const seek = useCallback(
    (ms: number) => {
      const video = videoRef.current;
      if (!video) return;
      video.currentTime = Math.min(Math.max(0, ms), game.duration_ms) / 1000;
    },
    [game.duration_ms],
  );

  // Seek and bring the film into view (highlight review sits below the player).
  const seekAndShow = useCallback(
    (ms: number, trackId?: string) => {
      seek(ms);
      if (trackId) setSelectedTrack(trackId);
      videoRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    },
    [seek],
  );

  const { refresh } = room;
  const refreshRoom = useCallback(() => void refresh(), [refresh]);

  const job = room.activeJob;
  const active = !!job && !isTerminal(job);

  return (
    <div className="room">
      <div className="room-head">
        <div>
          <h2>{game.title}</h2>
          <p className="muted small">
            {game.sport} · {game.width}×{game.height} · {Number(game.fps).toFixed(2)} fps · {formatMs(game.duration_ms)}
            {game.codec ? ` · ${game.codec}` : ''} · <code title={game.game_id}>{game.game_id.slice(0, 8)}</code>
          </p>
        </div>
        <div className="room-actions">
          <span className={`stream ${room.streamState}`}>
            <i /> Updates: {STREAM_LABEL[room.streamState]}
          </span>
          <button onClick={() => void room.startAnalysis()} disabled={room.starting || active || !room.loaded}>
            {room.starting ? 'Submitting…' : active ? 'Analysis running' : job ? 'Re-run analysis' : 'Analyze footage'}
          </button>
        </div>
      </div>

      {game.sample && (
        <p className="attribution small">
          Sample footage: <a href="https://github.com/open-starlab/TrackID3x3" target="_blank" rel="noreferrer">TrackID3x3</a> (Yamada et al.), licensed{' '}
          <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer">CC BY 4.0</a>. Used for local prototype testing.
        </p>
      )}
      {room.loadError && (
        <div className="alert error">
          {room.loadError}
          <button className="link" onClick={() => void room.refresh()}>Retry</button>
        </div>
      )}
      {room.startError && <div className="alert error">Could not start analysis: {room.startError}</div>}
      {room.streamState !== 'live' && room.streamState !== 'connecting' && (
        <div className="alert warn">Live update stream is {room.streamState === 'closed' ? 'closed' : 'reconnecting'}; job status falls back to polling. Data refreshes on reconnect.</div>
      )}

      <div className="room-grid">
        <section className="panel film">
          <FilmPlayer game={game} videoRef={videoRef} playheadMs={playheadMs} index={index} showBoxes={showBoxes} showPose={showPose} selectedTrack={selectedTrack} />
          <div className="controls">
            <label className="toggle"><input type="checkbox" checked={showBoxes} onChange={(e) => setShowBoxes(e.target.checked)} /> Boxes</label>
            <label className="toggle"><input type="checkbox" checked={showPose} onChange={(e) => setShowPose(e.target.checked)} /> Pose (COCO-17)</label>
            <label className="select">
              Track
              <select value={selectedTrack ?? ''} onChange={(e) => setSelectedTrack(e.target.value || null)} disabled={!index.trackIds.length}>
                <option value="">All tracks ({index.trackIds.length})</option>
                {index.trackIds.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.playerLabel ? `${t.playerLabel} · ` : ""}T{t.id} · {t.count} samples · {formatMs(t.first_ms)}–{formatMs(t.last_ms)}
                  </option>
                ))}
              </select>
            </label>
            <span className="muted small playhead-readout">{formatMs(playheadMs)}</span>
          </div>
          <Timeline durationMs={game.duration_ms} playheadMs={playheadMs} index={index} clips={clips} selectedTrack={selectedTrack} onSeek={seek} />
          <p className="fineprint">
            Boxes and keypoints are raw person-detector/pose predictions on sampled frames, shown at the nearest prior sample within 400 ms — never interpolated.
            Player labels reconnect temporary track fragments using uniform appearance and motion; these associations can be wrong and remain provisional. Tracks can include referees or spectators. No names or jersey numbers are inferred.
            Keypoint dots show the model's visibility flag, not a calibrated confidence.
          </p>
        </section>

        <div className="side-col">
          <JobPanel job={room.activeJob} jobs={room.jobs} loaded={room.loaded} refreshing={room.refreshing} progress={room.progress} durationMs={game.duration_ms} sampleCount={room.tracks.size} trackCount={index.trackIds.length} delay={room.delayStats} ignored={room.ignored} />
          {index.trackIds.length > 0 && (
            <section className="panel">
              <h3>Temporary tracks</h3>
              <p className="muted small">Click to isolate. Counts are model samples, not players.</p>
              <div className="chips">
                {index.trackIds.slice(0, 40).map((t) => (
                  <button key={t.id} className={selectedTrack === t.id ? 'chip on' : 'chip'} style={{ ['--c' as string]: trackColor(t.id) }} onClick={() => setSelectedTrack(selectedTrack === t.id ? null : t.id)}>
                    T{t.id} <small>{t.count}</small>
                  </button>
                ))}
                {index.trackIds.length > 40 && <span className="muted small">+{index.trackIds.length - 40} more in selector</span>}
              </div>
            </section>
          )}
        </div>
      </div>

      <HighlightsPanel gameId={game.game_id} jobId={job?.job_id ?? null} analysisActive={active} onSeek={seekAndShow} onUpdated={refreshRoom} />

      <ClipsPanel clips={clips} events={events} job={job} onSeek={seek} />

      <section className="panel">
        <h3>Profiles</h3>
        <p className="muted small">
          No athlete profiles exist yet. Profiles will require a verified roster mapping and explicit consent; tracks are never auto-linked to people.
        </p>
      </section>
    </div>
  );
}

const fmt = (n: number | undefined, digits = 1) => (typeof n === 'number' && Number.isFinite(n) ? n.toFixed(digits) : '–');

function JobPanel(props: {
  job: Job | null;
  jobs: Job[];
  loaded: boolean;
  refreshing: boolean;
  progress: { processed_frames?: number; source_timestamp_ms: number } | null;
  durationMs: number;
  sampleCount: number;
  trackCount: number;
  delay: DelayStats;
  ignored: number;
}) {
  const { job, jobs, loaded, progress, durationMs, delay } = props;
  const report = job?.report;
  const pct = progress ? Math.min(100, (progress.source_timestamp_ms / Math.max(1, durationMs)) * 100) : job?.status === 'succeeded' ? 100 : 0;

  return (
    <section className="panel">
      <div className="panel-head">
        <h3>Analysis job</h3>
        {props.refreshing && <span className="muted small">refreshing…</span>}
      </div>
      {!loaded && !job ? (
        <p className="muted small">Loading job history…</p>
      ) : !job ? (
        <p className="muted small">No analysis yet. Click <b>Analyze footage</b> to run person detection, pose and tracking (stride 5, offline pass — not replay-paced).</p>
      ) : (
        <>
          <div className="job-line">
            <span className={`status ${job.status}`}>{job.status}</span>
            <span>phase <b>{job.phase}</b></span>
            <span>attempt <b>{job.attempts}</b>/3</span>
          </div>
          {job.error && <div className={job.status === 'failed' ? 'alert error' : 'alert warn'}>{job.error}</div>}
          {!isTerminal(job) && (
            <div className="progress" aria-label="Source position processed">
              <div style={{ width: `${pct}%` }} />
              <span>{progress ? `processed to ${formatMs(progress.source_timestamp_ms)} / ${formatMs(durationMs)}` : 'waiting for first frames…'}</span>
            </div>
          )}
          <dl className="metrics">
            <div><dt>Track samples received</dt><dd>{props.sampleCount}</dd></div>
            <div><dt>Temporary track IDs</dt><dd>{props.trackCount}</dd></div>
            {progress?.processed_frames !== undefined && !report && <div><dt>Frames processed</dt><dd>{progress.processed_frames}</dd></div>}
            {report && (
              <>
                <div><dt>Inference device</dt><dd>{report.execution_mode ?? report.device ?? "cpu (legacy run)"}</dd></div>
                <div><dt>Model load</dt><dd>{fmt(report.model_load_seconds ?? 0, 2)} s{report.model_cache_hit ? " · cached" : ""}</dd></div>
                <div><dt>Inference FPS (measured)</dt><dd>{fmt(report.inference_fps, 2)}</dd></div>
                <div><dt>Processed / decoded frames</dt><dd>{report.processed_frames}{report.decoded_frames !== undefined ? ` / ${report.decoded_frames}` : ''}</dd></div>
                <div><dt>Stride</dt><dd>every {report.stride} frame{report.stride === 1 ? '' : 's'}</dd></div>
                <div><dt>Wall time</dt><dd>{fmt(report.elapsed_seconds, 1)} s</dd></div>
                <div><dt>Realtime factor</dt><dd>{fmt(report.realtime_factor, 2)}×</dd></div>
                <div><dt>Report track IDs</dt><dd>{report.track_ids}</dd></div>
              </>
            )}
          </dl>
          {report?.model && (
            <p className="fineprint">
              Model <code>{report.model}</code>
              {report.tracker ? <> · tracker <code>{report.tracker}</code></> : null}. Inference FPS covers model time only.
            </p>
          )}
          <div className="delay">
            <h4>Update transport delay</h4>
            {delay ? (
              <p>
                last <b>{delay.last} ms</b> · median <b>{delay.median} ms</b> <span className="muted">over {delay.count} live updates</span>
              </p>
            ) : (
              <p className="muted small">Measured only for updates written after this page connected.</p>
            )}
            <p className="fineprint">Server write → browser receipt (same machine clock, includes stream poll interval). This is not inference latency.</p>
          </div>
          {props.ignored > 0 && <p className="fineprint">Ignored {props.ignored} replayed updates from older jobs/attempts.</p>}
          {jobs.length > 1 && (
            <details className="history">
              <summary>Job history ({jobs.length})</summary>
              <ol>
                {jobs.map((j) => (
                  <li key={j.job_id}>
                    <code>{j.job_id.slice(0, 8)}</code> <span className={`status ${j.status}`}>{j.status}</span> {j.attempts} attempt{j.attempts === 1 ? '' : 's'}
                  </li>
                ))}
              </ol>
            </details>
          )}
        </>
      )}
    </section>
  );
}

const ORIGIN_LABEL: Record<string, string> = {
  review_window: 'Review window — fixed time split, not a detected play',
  coach_annotation: 'Coach-supplied time range',
  visual_heuristic: 'Provisional visual activity — heuristic pick, not a verified play',
  reviewer_confirmed: 'Reviewer-confirmed outcome — human review, not an official stat',
};

const eventTypeText = (t: string) => EVENT_TYPE_LABEL[t as keyof typeof EVENT_TYPE_LABEL] ?? t.replace(/_/g, ' ');

/** One-line provenance for a clip; never presents heuristic activity as a confirmed outcome. */
function clipDescription(c: Clip, ev: GameEvent | undefined): string {
  if (c.kind === 'highlight') {
    const base =
      ev?.origin === 'reviewer_confirmed' && ev.event_type !== 'visual_activity'
        ? `Reviewer-confirmed: ${eventTypeText(ev.event_type)} (not an official stat)`
        : c.review_status === 'approved'
        ? 'Reviewer-approved visual activity · no outcome claimed'
        : 'Provisional visual activity · heuristic highlight pick, unverified';
    return `${base} · review ${c.review_status ?? 'pending'}`;
  }
  if (!ev) return 'Segment';
  const origin = ORIGIN_LABEL[ev.origin] ?? ev.origin;
  // Review windows keep their raw event_type; heuristic events are always labelled as provisional above.
  return ev.event_type && ev.origin !== 'visual_heuristic' ? `${origin} · ${eventTypeText(ev.event_type)}` : origin;
}

function ClipsPanel({ clips, events, job, onSeek }: { clips: Clip[]; events: Record<string, GameEvent>; job: Job | null; onSeek: (ms: number) => void }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>Clips</h3>
        <span className="muted small">Ranges are in original source time</span>
      </div>
      {clips.length === 0 ? (
        <p className="muted small">
          {!job ? 'Run analysis to render review clips.' : job.status === 'failed' ? 'No clips: the analysis failed.' : isTerminal(job) ? 'This job produced no clips.' : 'Clips render after tracking completes.'}
        </p>
      ) : (
        <div className="clips">
          {clips.map((c, i) => {
            const ev = c.event_id ? events[c.event_id] : undefined;
            const highlight = c.kind === 'highlight';
            return (
              <article key={c.clip_id} className={highlight ? 'clip highlight' : 'clip'}>
                <video src={c.uri} controls preload="auto" playsInline />
                <div className="clip-body">
                  <div className="clip-title">
                    <strong>
                      {highlight ? 'Highlight pick' : `Clip ${i + 1}`}
                      {c.track_id ? <code className="clip-track"> T{c.track_id}</code> : null}
                    </strong>
                    <span className={`qa ${c.qa_status === 'passed' ? 'ok' : 'bad'}`}>QA {c.qa_status}</span>
                  </div>
                  <p className="range">
                    {formatMs(c.source_start_ms)} → {formatMs(c.source_end_ms)} <span className="muted">({((c.source_end_ms - c.source_start_ms) / 1000).toFixed(2)} s)</span>
                  </p>
                  {c.measured_duration_ms !== undefined && (
                    <p className="fineprint">
                      ffprobe {c.measured_duration_ms} ms vs expected {c.expected_duration_ms} ms (±{c.tolerance_ms} ms)
                    </p>
                  )}
                  {highlight && typeof c.highlight_score === 'number' && Number.isFinite(c.highlight_score) && (
                    <p className="small">
                      Highlight priority <b>{Math.round(c.highlight_score)}</b>/100 <span className="muted">· footage-review order, not a talent rating</span>
                    </p>
                  )}
                  <p className="fineprint">
                    {clipDescription(c, ev)} · {c.identity_verified ? 'identity verified' : 'identity unverified'}
                  </p>
                  <div className="clip-actions">
                    <button className="secondary small" onClick={() => onSeek(c.source_start_ms)}>Seek source</button>
                    <a href={c.uri} target="_blank" rel="noreferrer">Open clip ↗</a>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
