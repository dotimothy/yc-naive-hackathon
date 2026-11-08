import { SectionTabs } from './SectionTabs';
import { ShotSegmentsPanel } from './ShotSegmentsPanel';
import { StrictMode, useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { api, ApiError, request } from './api';
import { buildFrameIndex, FilmPlayer, formatMs, Timeline, trackColor, usePlayhead } from './FilmPlayer';
import { EVENT_TYPE_LABEL, HighlightsPanel } from './HighlightsPanel';
import { BasketballPanel, type BallObject } from './BasketballPanel';
import { LiveBoard } from './LiveBoard';
import { NaiveDashboard } from './NaiveDashboard';
import type { CameraShot, Clip, Game, GameEvent, Job } from './types';
import { isTerminal, useGameRoom, type DelayStats } from './useGameRoom';
import './styles.css';

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

// Latest analysis job per game, for the library badges. `null` = never analyzed.
type LibraryStatus = Record<string, Job | null>;

function useLibraryStatus(games: Game[] | null): LibraryStatus {
  const [status, setStatus] = useState<LibraryStatus>({});
  const ids = (games ?? []).map((g) => g.game_id).join(',');
  const anyActive = Object.values(status).some((j) => !!j && !isTerminal(j));

  useEffect(() => {
    if (!ids) return;
    const ctrl = new AbortController();
    const refresh = async () => {
      const entries = await Promise.all(
        ids.split(',').map(async (id) => {
          try {
            const jobs = await api.listJobs(id, ctrl.signal);
            return [id, jobs[jobs.length - 1] ?? null] as const;
          } catch {
            return null; // keep the previous badge on a transient error
          }
        }),
      );
      if (ctrl.signal.aborted) return;
      setStatus((prev) => {
        const next = { ...prev };
        for (const e of entries) if (e) next[e[0]] = e[1];
        return next;
      });
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), anyActive ? 3000 : 15000);
    return () => {
      ctrl.abort();
      window.clearInterval(timer);
    };
  }, [ids, anyActive]);

  return status;
}

function libraryBadge(job: Job | null | undefined): { cls: string; text: string } {
  if (job === undefined) return { cls: 'none', text: 'checking…' };
  if (job === null) return { cls: 'none', text: 'not analyzed' };
  if (job.status === 'succeeded') return { cls: 'succeeded', text: 'processed' };
  if (job.status === 'running') return { cls: 'running', text: `processing · ${job.phase}` };
  if (job.status === 'queued') return { cls: 'queued', text: 'waiting' };
  if (job.status === 'cancelling') return { cls: 'queued', text: 'cancelling' };
  if (job.status === 'cancelled') return { cls: 'none', text: 'cancelled' };
  return { cls: 'failed', text: 'failed' };
}

const APP_VIEWS = [{ id: 'film', label: 'Film Room' }, { id: 'naive', label: 'Naive Crew' }] as const;
type AppView = typeof APP_VIEWS[number]['id'];

function App() {
  const [games, setGames] = useState<Game[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<AppView>(() => (window.location.hash === '#naive' ? 'naive' : 'film'));
  const changeView = (v: AppView) => { setView(v); window.history.replaceState(null, '', v === 'naive' ? '#naive' : window.location.pathname + window.location.search); };

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
  const library = useLibraryStatus(games);
  const jobsKnown = Object.values(library);
  const counts = {
    done: jobsKnown.filter((j) => j?.status === 'succeeded').length,
    running: jobsKnown.filter((j) => j?.status === 'running').length,
    waiting: jobsKnown.filter((j) => j?.status === 'queued').length,
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          <div>
            <h1>PlayLattice</h1>
            <p>Film Room</p>
          </div>
        </div>
        <div className="topbar-meta">
          <div className="view-switch"><SectionTabs id="view" label="Workspace" tabs={APP_VIEWS} value={view} onChange={changeView} /></div>
          <span className="pill sport">Basketball</span>
        </div>
      </header>
      <div className="naive-view" id="view-panel-naive" role="tabpanel" aria-labelledby="view-tab-naive" hidden={view !== 'naive'}>
        <NaiveDashboard active={view === 'naive'} />
      </div>
      <div className="layout" id="view-panel-film" role="tabpanel" aria-labelledby="view-tab-film" hidden={view !== 'film'}>
        <aside className="sidebar">
          <IngestPanel onGame={addGame} />
          <section className="panel">
            <div className="panel-head">
              <h2>Game Library</h2>
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
              <>
              <p className="library-summary small">
                <span className="ok">{counts.done} processed</span> · <span className="info">{counts.running} processing</span> · <span>{counts.waiting} waiting</span>
              </p>
              <ul className="game-list">
                {[...games].reverse().map((g) => (
                  <li key={g.game_id}>
                    <button className={g.game_id === selectedId ? 'game active' : 'game'} onClick={() => setSelectedId(g.game_id)}>
                      <div className="game-title">
                        <strong>{g.title}</strong>
                        {(() => {
                          const b = libraryBadge(library[g.game_id]);
                          return <em className={`status ${b.cls}`}>{b.text}</em>;
                        })()}
                      </div>
                      <span>
                        {g.sport} · {formatMs(g.duration_ms)} · {g.width}×{g.height} · {Number(g.fps).toFixed(2)} fps
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              </>
            )}
          </section>
        </aside>
        <main className="main">
          {selected ? (
            <GameRoom key={selected.game_id} game={selected} />
          ) : (
            <div className="empty">
              <CourtArt />
              <span className="empty-kicker">Tip-Off</span>
              <h2>No Game Selected</h2>
              <p>Upload basketball footage you are authorized to use, or load the TrackID3x3 sample, then run one analysis to see player tracking, pose and ball/hoop observations.</p>
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
      <h2>Add Footage</h2>
      <p className="muted small">Upload once. Tracking, analysis and highlight clips are generated automatically; progress and finished outputs appear in the film room.</p>
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
          {busy === 'upload' ? (progress !== null && progress < 1 ? `Uploading ${Math.round(progress * 100)}%` : 'Preparing Analysis…') : 'Upload & Analyze'}
        </button>
        {file && !rights && <p className="muted small">Confirm footage rights to enable upload.</p>}
      </form>
      <div className="divider"><span>or</span></div>
      <button className="secondary" onClick={() => void sample()} disabled={busy !== null}>
        {busy === 'sample' ? 'Preparing Sample…' : 'Analyze TrackID3x3 Sample'}
      </button>
      {error && <div className="alert error">{error}</div>}
    </section>
  );
}

const ROOM_TABS = [
  { id: 'film', label: 'Film' }, { id: 'highlights', label: 'Players & Highlights' },
  { id: 'shots', label: 'Camera Shots' }, { id: 'ball', label: 'Ball & Hoop' },
  { id: 'clips', label: 'Clips' }, { id: 'analysis', label: 'Analysis Details' },
] as const;
type RoomTab = typeof ROOM_TABS[number]['id'];

function GameRoom({ game }: { game: Game }) {
  const room = useGameRoom(game.game_id);
  const [tab, setTab] = useState<RoomTab>('film');
  const videoRef = useRef<HTMLVideoElement>(null);
  const playheadMs = usePlayhead(videoRef);
  const [correctionError, setCorrectionError] = useState('');
  const [correcting, setCorrecting] = useState(false);
  const [shotSegments, setShotSegments] = useState<CameraShot[]>([]);
  const [ballObjects, setBallObjects] = useState<BallObject[]>([]);
  const [showObjects, setShowObjects] = useState(true);
  const [highlightsRevision, setHighlightsRevision] = useState(0);
  const [showBoxes, setShowBoxes] = useState(true);
  const [showPose, setShowPose] = useState(true);
  const [showFootDot, setShowFootDot] = useState(true);
  const [interpolate, setInterpolate] = useState(true);
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

  // Seeking from any review tab returns to the film without remounting the video.
  const seekAndShow = useCallback(
    (ms: number, trackId?: string) => {
      setTab('film');
      seek(ms);
      if (trackId) setSelectedTrack(trackId);
      requestAnimationFrame(() => videoRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
    },
    [seek],
  );

  // Read on demand so the highlights panel does not re-render at playback rate.
  useEffect(() => {
    document.querySelectorAll<HTMLVideoElement>('.room [role="tabpanel"][hidden] video').forEach(video => video.pause());
  }, [tab]);

  const getPlayheadMs = useCallback(() => Math.round((videoRef.current?.currentTime ?? 0) * 1000), []);

  const { refresh } = room;
  const refreshRoom = useCallback(() => void refresh(), [refresh]);

  const correctIdentity = async (undo = false) => {
    setCorrecting(true); setCorrectionError('');
    try {
      const sample = [...room.tracks.values()].find(s => s.track_id === selectedTrack);
      const sourceTrack = (sample as { source_track_id?: string } | undefined)?.source_track_id ?? selectedTrack;
      await request(`/api/games/${game.game_id}/highlights/identity/splits`, undo ? { method: 'DELETE' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ track_id: sourceTrack, split_ms: getPlayheadMs() }) });
      setSelectedTrack(null); setHighlightsRevision(v => v + 1); await refresh();
    } catch (e) { setCorrectionError((e as Error).message); }
    finally { setCorrecting(false); }
  };

  const job = room.activeJob;
  const active = !!job && !isTerminal(job);
  // The submitted input completes directly into its outputs; no Generate/Review click is required.
  useEffect(() => {
    if (job?.status === 'succeeded' && job.job_id === game.analysis_job_id) setTab('clips');
  }, [job?.status, job?.job_id, game.analysis_job_id]);

  return (
    <div className="room">
      <LiveBoard refreshKey={highlightsRevision}
        game={game}
        job={job}
        tracks={room.tracks}
        clips={clips}
        progress={room.progress}
        streamState={room.streamState}
        delay={room.delayStats}
        onPick={(trackId, ms) => seekAndShow(ms, trackId)}
        actions={<>
          <button className="cta" onClick={() => void room.startAnalysis()} disabled={room.starting || active || !room.loaded}>
            {room.starting ? 'Submitting…' : job?.status === 'cancelling' ? 'Cancelling…' : active ? 'Analysis Running' : job ? 'Re-Run Analysis' : 'Analyze Footage'}
          </button>
          {active && <button className="secondary danger" onClick={() => void room.cancelAnalysis()} disabled={room.cancelling || job?.status === 'cancelling'}>{room.cancelling || job?.status === 'cancelling' ? 'Cancelling…' : 'Cancel Analysis'}</button>}
        </>}
      />

      {game.sample && (
        <p className="attribution small">
          Sample footage: <a href="https://github.com/open-starlab/TrackID3x3" target="_blank" rel="noreferrer">TrackID3x3</a> (Yamada et al.), licensed{' '}
          <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer">CC BY 4.0</a>.
        </p>
      )}
      {room.loadError && (
        <div className="alert error">
          {room.loadError}
          <button className="link" onClick={() => void room.refresh()}>Retry</button>
        </div>
      )}
      {room.startError && <div className="alert error">Could not start analysis: {room.startError}</div>}
      {game.analysis_error && (!job || job.job_id === game.analysis_job_id) && <div className="alert error" role="alert">Video uploaded, but automatic analysis could not start: {game.analysis_error}</div>}
      {room.cancelError && <div className="alert error" role="alert">Could not cancel analysis: {room.cancelError}</div>}
      {job?.status === 'cancelled' && <p className="alert warn" role="status">Analysis cancelled. Partial results were cleared. You can run analysis again.</p>}
      {room.streamState !== 'live' && room.streamState !== 'connecting' && (
        <div className="alert warn">Live update stream is {room.streamState === 'closed' ? 'closed' : 'reconnecting'}; job status falls back to polling. Data refreshes on reconnect.</div>
      )}

      <SectionTabs id="room" label="Film room sections" tabs={ROOM_TABS} value={tab} onChange={setTab} />
      <div id="room-panel-film" role="tabpanel" aria-labelledby="room-tab-film" hidden={tab !== 'film'}>
      <div className="room-grid">
        <section className="panel film">
          <FilmPlayer game={game} videoRef={videoRef} playheadMs={playheadMs} index={index} showBoxes={showBoxes} showPose={showPose} showFootDot={showFootDot} interpolate={interpolate} selectedTrack={selectedTrack} objects={showObjects ? ballObjects : []} shots={shotSegments} />
          <div className="controls">
            {active && <button className="secondary small danger" onClick={() => void room.cancelAnalysis()} disabled={room.cancelling || job?.status === 'cancelling'}>{room.cancelling || job?.status === 'cancelling' ? 'Cancelling…' : 'Cancel Analysis'}</button>}
            <label className="toggle"><input type="checkbox" checked={showObjects} onChange={e => setShowObjects(e.target.checked)} /> Ball / hoop</label>
            <label className="toggle"><input type="checkbox" checked={showBoxes} onChange={(e) => setShowBoxes(e.target.checked)} /> Boxes</label>
            <label className="toggle"><input type="checkbox" checked={showPose} onChange={(e) => setShowPose(e.target.checked)} /> Pose (COCO-17)</label>
            <label className="toggle"><input type="checkbox" checked={showFootDot} onChange={(e) => setShowFootDot(e.target.checked)} /> Foot dot</label>
            <label className="toggle" title="Blend boxes/pose between model samples so they follow the video between sampled frames. Display smoothing only."><input type="checkbox" checked={interpolate} onChange={(e) => setInterpolate(e.target.checked)} /> Interpolate between samples</label>
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
          <div className="controls">
            <button className="secondary small" disabled={!selectedTrack || active || correcting} onClick={() => void correctIdentity()}>Split selected track at playhead</button>
            <button className="ghost small" disabled={active || correcting || job?.status !== 'succeeded'} onClick={() => void correctIdentity(true)}>Undo identity splits</button>
            <span className="fineprint">Select a track and seek to a swap. Splits snap to a sampled frame and clear saved player groups, reviews and highlight clips for this analysis.</span>
          </div>
          {correctionError && <div className="alert error">{correctionError}</div>}
          <Timeline durationMs={game.duration_ms} playheadMs={playheadMs} index={index} clips={clips} selectedTrack={selectedTrack} onSeek={seek} />
          <p className="fineprint">
            Boxes and keypoints are raw person-detector/pose predictions on sampled frames, shown at the nearest prior sample within 400 ms — never interpolated.
            New analysis keeps uncertain fragments separate; proposed reconnections require review. Player labels remain provisional. Tracks can include referees or spectators. No names or jersey numbers are inferred.
            “A?/B?” tags are an anonymous colour grouping and “non-player?/role?” tags are review prompts (dashed box); neither is verified and nobody is hidden automatically.
            Keypoint dots use a visibility filter; model scores are not calibrated certainty.
          </p>
        </section>

        <div className="side-col">

          {index.trackIds.length > 0 && (
            <section className="panel">
              <h3>Temporary Tracks</h3>
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

      </div>
      <div id="room-panel-shots" role="tabpanel" aria-labelledby="room-tab-shots" hidden={tab !== 'shots'}>
      <ShotSegmentsPanel onShots={setShotSegments} gameId={game.game_id} jobId={job?.job_id ?? null} jobStatus={job?.status} analysisActive={active} refreshKey={highlightsRevision} onSeek={(ms) => { setSelectedTrack(null); seekAndShow(ms); }} onUpdated={() => { refreshRoom(); setHighlightsRevision(v => v + 1); }} />

      </div>
      <div id="room-panel-ball" role="tabpanel" aria-labelledby="room-tab-ball" hidden={tab !== 'ball'}>
      <BasketballPanel analysisStatus={job?.status} refreshKey={highlightsRevision} gameId={game.game_id} jobId={job?.job_id ?? null} analysisActive={active} tracks={index.trackIds} onSeek={seekAndShow} onObjects={setBallObjects} onImported={() => { setHighlightsRevision(v => v + 1); refreshRoom(); }} />

      </div>
      <div id="room-panel-highlights" role="tabpanel" aria-labelledby="room-tab-highlights" hidden={tab !== 'highlights'}>
      {job?.status !== 'cancelled' && job?.status !== 'cancelling' && <HighlightsPanel identityMergeSuggestions={job?.report?.identity_merge_suggestions} refreshKey={highlightsRevision} gameId={game.game_id} jobId={job?.job_id ?? null} analysisActive={active} onSeek={seekAndShow} onUpdated={() => { refreshRoom(); setHighlightsRevision(v => v + 1); }} getPlayheadMs={getPlayheadMs} />}
      {(job?.status === 'cancelled' || job?.status === 'cancelling') && <p className="panel muted">Highlights will be available after a completed analysis.</p>}

      </div>
      <div id="room-panel-clips" role="tabpanel" aria-labelledby="room-tab-clips" hidden={tab !== 'clips'}>
      <ClipsPanel clips={clips} events={events} job={job} onSeek={seekAndShow} />
      </div>
      <div id="room-panel-analysis" role="tabpanel" aria-labelledby="room-tab-analysis" hidden={tab !== 'analysis'}>
          <JobPanel job={room.activeJob} jobs={room.jobs} loaded={room.loaded} refreshing={room.refreshing} progress={room.progress} durationMs={game.duration_ms} sampleCount={room.tracks.size} trackCount={index.trackIds.length} delay={room.delayStats} ignored={room.ignored} />
      </div>


    </div>
  );
}

const fmt = (n: number | undefined, digits = 1) => (typeof n === 'number' && Number.isFinite(n) ? n.toFixed(digits) : '–');

function JobPanel(props: {
  job: Job | null;
  jobs: Job[];
  loaded: boolean;
  refreshing: boolean;
  progress: { processed_frames?: number; basketball_processed_frames?: number; source_timestamp_ms: number } | null;
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
        <h3>Analysis Job</h3>
        {props.refreshing && <span className="muted small">refreshing…</span>}
      </div>
      {!loaded && !job ? (
        <p className="muted small">Loading job history…</p>
      ) : !job ? (
        <p className="muted small">No analysis yet. Click <b>Analyze Footage</b> to run player detection, pose, tracking and the available ball/hoop detector in one coordinated video pass.</p>
      ) : (
        <>
          <div className="job-line">
            <span className={`status ${job.status}`}>{job.status}</span>
            <span>phase <b>{job.phase}</b></span>
            <span>attempt <b>{job.attempts}</b>/3</span>
          </div>
          {job.error && <div className={job.status === 'failed' ? 'alert error' : 'alert warn'}>{job.error}</div>}
          {!isTerminal(job) && (
            <div className="progress" aria-label="Shared source position processed">
              <div style={{ width: `${pct}%` }} />
              <span>{progress ? `shared source processed to ${formatMs(progress.source_timestamp_ms)} / ${formatMs(durationMs)}` : 'waiting for first frames…'}</span>
            </div>
          )}
          <dl className="metrics">
            <div><dt>Track samples received</dt><dd>{props.sampleCount}</dd></div>
            <div><dt>Temporary track IDs</dt><dd>{props.trackCount}</dd></div>
            {progress?.processed_frames !== undefined && !report && <div><dt>Player frames sampled</dt><dd>{progress.processed_frames}</dd></div>}
            {progress?.basketball_processed_frames !== undefined && !report && <div><dt>Ball/hoop frames sampled</dt><dd>{progress.basketball_processed_frames}</dd></div>}
            {report && (
              <>
                <div><dt>Inference device</dt><dd>{report.execution_mode ?? report.device ?? "cpu (legacy run)"}</dd></div>
                <div><dt>Model load</dt><dd>{fmt(report.model_load_seconds ?? 0, 2)} s{report.model_cache_hit ? " · cached" : ""}</dd></div>
                <div><dt>Player inference FPS (measured)</dt><dd>{fmt(report.inference_fps, 2)}</dd></div>
                <div><dt>Player sampled / decoded frames</dt><dd>{report.processed_frames}{report.decoded_frames !== undefined ? ` / ${report.decoded_frames}` : ''}</dd></div>
                <div><dt>Player sampling interval</dt><dd>every {report.stride} frame{report.stride === 1 ? '' : 's'}</dd></div>
                <div><dt>Wall time</dt><dd>{fmt(report.elapsed_seconds, 1)} s</dd></div>
                <div><dt>Realtime factor</dt><dd>{fmt(report.realtime_factor, 2)}×</dd></div>
                <div><dt>Report track IDs</dt><dd>{report.track_ids}</dd></div>
              </>
            )}
          </dl>
          {report && <TrackerReviewSummary report={report} />}
          {report?.model && (
            <p className="fineprint">
              Model <code>{report.model}</code>
              {report.tracker ? <> · tracker <code>{report.tracker}</code></> : null}. Player inference FPS covers player-model time only. Ball evidence reports its own cadence and timing; both share source timestamps.
            </p>
          )}
          <div className="delay">
            <h4>Update Transport Delay</h4>
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
              <summary>Job History ({jobs.length})</summary>
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

const countOf = (v: unknown): number | null =>
  Array.isArray(v) ? v.length : v && typeof v === 'object' ? Object.keys(v).length : null;

/** Optional tracker review metadata; shown as counts to review, never as facts. */
function TrackerReviewSummary({ report }: { report: Job['report'] & object }) {
  const roleEntries = Array.isArray(report.role_flags)
    ? report.role_flags
    : report.role_flags && typeof report.role_flags === 'object' ? Object.values(report.role_flags) : null;
  const roles = roleEntries?.filter((entry) => {
    if (!entry || typeof entry !== 'object' || !('role_status' in entry)) return false;
    return entry.role_status === 'review_required' || entry.role_status === 'likely_non_player';
  }).length ?? null;
  const merges = countOf(report.merge_proposals);
  const summary = report.team_summary && typeof report.team_summary === 'object' ? report.team_summary : null;
  const teamReason = summary && typeof summary.reason === 'string' ? summary.reason.replace(/_/g, ' ') : null;
  if (roles === null && merges === null && !summary) return null;
  return (
    <p className="fineprint">
      Tracker review prompts:{teamReason ? ` colour grouping — ${teamReason};` : ''}
      {roles !== null ? ` ${roles} role flag${roles === 1 ? '' : 's'};` : ''}
      {merges !== null ? ` ${merges} unconfirmed merge proposal${merges === 1 ? '' : 's'}.` : ''} Heuristic only; confirm in footage.
    </p>
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
        ? `Reviewer-approved: ${eventTypeText(ev.event_type)} (not an official stat)`
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
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [job?.job_id]);
  const pages = Math.max(1, Math.ceil(clips.length / 4));
  const current = Math.min(page, pages - 1);
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>Clips</h3>
        <span className="muted small">Ranges are in original source time</span>
      </div>
      {job?.report?.automatic_output && <p className="alert" role="status">{job.report.automatic_output.reason}</p>}
      {clips.length === 0 ? (
        <p className="muted small">
          {!job ? 'Run analysis to render review clips.' : job.status === 'cancelled' ? 'Analysis cancelled. Run analysis again to generate clips.' : job.status === 'failed' ? 'No clips: the analysis failed.' : isTerminal(job) ? 'This job produced no clips.' : 'Clips render after tracking completes.'}
        </p>
      ) : (
        <div className="clips">
          {clips.slice(current * 4, current * 4 + 4).map((c, i) => {
            const ev = c.event_id ? events[c.event_id] : undefined;
            const highlight = c.kind === 'highlight';
            return (
              <article key={c.clip_id} className={highlight ? 'clip highlight' : 'clip'}>
                <video src={c.uri} controls preload="auto" playsInline />
                <div className="clip-body">
                  <div className="clip-title">
                    <strong>
                      {highlight ? 'Highlight Pick' : `Clip ${current * 4 + i + 1}`}
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
                      Clip priority <b>{Math.round(c.highlight_score)}</b>/100 <span className="muted">· editorial footage order, not basketball performance</span>
                    </p>
                  )}
                  <p className="fineprint">
                    {clipDescription(c, ev)} · {c.identity_verified ? 'identity verified' : 'identity unverified'}
                  </p>
                  <div className="clip-actions">
                    <button className="secondary small" onClick={() => onSeek(c.source_start_ms)}>Seek Source</button>
                    <a href={c.uri} target="_blank" rel="noreferrer">Open Clip ↗</a>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
      {clips.length > 0 && <div className="table-pagination"><span>Clips {current * 4 + 1}–{Math.min((current + 1) * 4, clips.length)} of {clips.length}</span>
        <button className="secondary small" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous clips</button>
        <button className="secondary small" disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>Next clips</button>
      </div>}
    </section>
  );
}

function BrandMark() {
  return <img className="logo" src="/favicon.png" width={48} height={48} alt="" aria-hidden="true" />;
}

function CourtArt() {
  return (
    <svg className="court-art" viewBox="0 0 280 150" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="1.5">
        <rect x="2" y="2" width="276" height="146" rx="2" />
        <path d="M140 2v146" />
        <circle cx="140" cy="75" r="18" />
        <rect x="2" y="50" width="52" height="50" />
        <rect x="226" y="50" width="52" height="50" />
        <path d="M2 14h16a62 62 0 0 1 0 122H2M278 14h-16a62 62 0 0 0 0 122h16" />
        <circle cx="54" cy="75" r="12" />
        <circle cx="226" cy="75" r="12" />
      </g>
      <circle cx="12" cy="75" r="3" fill="var(--accent)" />
      <circle cx="268" cy="75" r="3" fill="var(--accent)" />
    </svg>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
