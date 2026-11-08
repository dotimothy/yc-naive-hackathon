import { request } from './api';
import { useEffect, useRef, useState } from 'react';
import { formatMs } from './FilmPlayer';

export type BallObject = { timestamp_ms: number; class_name: string; bbox: [number, number, number, number]; confidence: number };
type Proposal = { candidate_id: string; start_ms: number; end_ms: number; approach_ms: number; evidence_quality: number };
type BallReport = {
  integrated?: boolean;
  processed_frames: number;
  frames_with_ball: number;
  stable_hoops?: unknown[];
  shot_candidates?: Proposal[];
  device?: string;
  elapsed_seconds?: number;
  coverage_scope?: string;
  analyzed_until_ms?: number;
  stride?: number;
};
type BallRun = {
  integrated?: boolean;
  identity_stale?: boolean;
  identity_revision?: number;
  run_id: string | null;
  status: string;
  parent_job_id: string | null;
  error?: string;
  report?: BallReport | null;
};
type ObjectResponse = { items: BallObject[]; run_id?: string; parent_job_id?: string };
const RUNNING = new Set(['queued', 'running']);

export function BasketballPanel({ gameId, jobId, analysisActive, tracks, onSeek, onObjects, onImported, refreshKey }: {
  refreshKey?: number; gameId: string; jobId: string | null; analysisActive: boolean; tracks: { id: string; playerLabel?: string }[];
  onSeek: (ms: number) => void; onObjects: (objects: BallObject[]) => void; onImported: () => void;
}) {
  const path = `/api/games/${encodeURIComponent(gameId)}/basketball`;
  const [run, setRun] = useState<BallRun | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [actor, setActor] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [imported, setImported] = useState<string[]>([]);
  const importAbort = useRef<AbortController | null>(null);
  const objectRun = useRef<string | null>(null);

  useEffect(() => {
    setRun(null);
    setError('');
    objectRun.current = null;
    onObjects([]);
  }, [path, jobId, onObjects]);

  // Reports and observations belong to one parent job. An aborted old request must never
  // put the previous video's objects back after the user changes games or starts a rerun.
  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const load = async () => {
      let polling = analysisActive;
      try {
        const next = await request<BallRun>(path, { signal: controller.signal });
        if (controller.signal.aborted) return;
        const current = !!jobId && next.parent_job_id === jobId;
        setRun(current ? next : null);
        polling ||= current && RUNNING.has(next.status);
        if (objectRun.current !== next.run_id) {
          objectRun.current = next.run_id;
          onObjects([]);
        }
        if (current && (RUNNING.has(next.status) || next.status === 'succeeded')) {
          const objects = await request<ObjectResponse>(`${path}/objects`, { signal: controller.signal });
          if (controller.signal.aborted) return;
          // New backends return scope metadata; retain compatibility with older item-only responses.
          const matches = (!objects.run_id || objects.run_id === next.run_id)
            && (!objects.parent_job_id || objects.parent_job_id === jobId);
          onObjects(matches ? objects.items : []);
        } else {
          onObjects([]);
        }
        setError('');
      } catch (e) {
        if (controller.signal.aborted) return;
        setError((e as Error).message);
      } finally {
        if (polling && !controller.signal.aborted) timer = window.setTimeout(() => void load(), 1500);
      }
    };
    void load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [path, jobId, analysisActive, refreshKey, onObjects]);

  // A new run or correction revision needs a fresh actor choice and confirmation.
  useEffect(() => {
    setActor('');
    setConfirmed(false);
    setImported([]);
    setBusy(false);
    importAbort.current?.abort();
    return () => importAbort.current?.abort();
  }, [gameId, jobId, run?.run_id, run?.identity_revision, run?.identity_stale]);

  const add = async (candidate: Proposal) => {
    importAbort.current?.abort();
    const controller = new AbortController();
    importAbort.current = controller;
    setBusy(true);
    setError('');
    try {
      await request(`${path}/candidates/${encodeURIComponent(candidate.candidate_id)}/review`, {
        method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_id: actor, actor_confirmed: confirmed }),
      });
      if (controller.signal.aborted) return;
      setImported(ids => [...ids, candidate.candidate_id]);
      onImported();
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  const currentRun = run?.parent_job_id === jobId && jobId !== null ? run : null;
  const report = currentRun?.report;
  const active = analysisActive || !!currentRun && RUNNING.has(currentRun.status);
  const stale = !!currentRun?.identity_stale;
  const proposals = report?.shot_candidates ?? [];
  const hoops = report?.stable_hoops ?? [];
  const unavailable = currentRun?.status === 'unavailable';
  const failed = currentRun?.status === 'failed';
  const legacy = !!report && currentRun?.integrated !== true && report.integrated !== true;
  const stateLabel = unavailable ? 'Detector unavailable' : failed ? 'Ball detector failed' : active ? legacy ? 'Ball detection running' : 'Analyzing with players' : currentRun?.status === 'succeeded' ? legacy ? 'Earlier pass complete' : 'Analysis complete' : 'Awaiting analysis';

  return <section className="panel basketball-panel">
    <div className="panel-head"><h3>Ball & Hoop Evidence</h3><span className={`status ${failed ? 'failed' : currentRun?.status === 'succeeded' ? 'succeeded' : active ? 'running' : ''}`}>{stateLabel}</span></div>
    <p className="muted small">Analyze Footage runs player tracking and ball/hoop detection in one shared video pass. Each model uses its own sampling interval. Trajectories propose plays for review; outcomes need confirmation.</p>
    {legacy && <p className="alert warn">These observations came from an earlier, separate ball/hoop pass. Run Analyze Footage to generate synchronized player and ball evidence together.</p>}
    {failed && <p className="alert warn">Ball/hoop analysis stopped before completion. Any observations below are partial; player analysis may continue. Run Analyze Footage again to retry both models.</p>}
    {error && <p className="alert error">{error}</p>}
    {currentRun?.error && <p className={unavailable ? 'alert warn' : 'alert error'}>{currentRun.error}</p>}
    {unavailable && !currentRun?.error && <p className="alert warn">Ball detection is currently unavailable. Player analysis can continue; no ball detections are claimed.</p>}
    {stale && <p className="alert warn">Player identities have changed. Run Analyze Footage again before assigning these plays.</p>}
    {!report || unavailable ? <p className="muted small">{unavailable ? 'No ball/hoop evidence was generated for this job.' : active ? 'Processing the shared frame stream. Observations appear as frames are analyzed.' : 'Run Analyze Footage to collect player and ball/hoop evidence together.'}</p> : <>
      <dl className="metrics">
        <div><dt>Frames with ball detections</dt><dd>{report.frames_with_ball}/{report.processed_frames}</dd></div>
        <div><dt>Stable hoop proposals</dt><dd>{hoops.length}</dd></div>
        <div><dt>Shot candidates</dt><dd>{proposals.length}{active ? ' · provisional' : ''}</dd></div>
        <div><dt>Made / missed</dt><dd>Unknown / unknown</dd></div>
        <div><dt>Ball device / elapsed time</dt><dd>{report.device ?? '—'} / {typeof report.elapsed_seconds === 'number' ? `${report.elapsed_seconds.toFixed(1)} s` : '—'}</dd></div>
        {typeof report.analyzed_until_ms === 'number' && <div><dt>Ball source position</dt><dd>{formatMs(report.analyzed_until_ms)}</dd></div>}
        {typeof report.stride === 'number' && <div><dt>Ball sampling interval</dt><dd>every {report.stride} frames</dd></div>}
      </dl>
      <p className="fineprint">Detection coverage is not accuracy. Ball and hoop boxes show observations within 200 ms before the playhead; no inferred flight between detections. {failed ? 'Partial evidence from an interrupted detector.' : active ? 'Coverage and proposals are still being collected.' : report.coverage_scope === 'partial_video' ? 'Partial source analyzed.' : ''}</p>
      {proposals.length === 0 ? <p className="muted small">{failed ? 'This incomplete detector run cannot establish whether shots occurred.' : active ? 'No shot candidates yet; the analysis is still running.' : 'No trajectories passed the proposal rules. This does not mean no shots occurred.'}</p> : <>
        <label className="select">Observed actor <select value={actor} disabled={active || stale} onChange={e => { setActor(e.target.value); setConfirmed(false); }}><option value="">Choose a current track</option>{tracks.map(t => <option key={t.id} value={t.id}>{t.playerLabel ?? 'Player unknown'} · T{t.id}</option>)}</select></label>
        <label className="check"><input type="checkbox" checked={confirmed} disabled={active || stale || !actor} onChange={e => setConfirmed(e.target.checked)} />I checked the source and this track performed the play.</label>
        {proposals.map(c => <article key={c.candidate_id} className="ball-candidate"><span>{formatMs(c.start_ms)}–{formatMs(c.end_ms)} · outcome unverified</span><button className="secondary small" onClick={() => onSeek(c.start_ms)}>Seek source</button><button className="small" disabled={busy || active || stale || currentRun?.status !== 'succeeded' || !actor || !confirmed || !jobId || imported.includes(c.candidate_id)} onClick={() => void add(c)}>{imported.includes(c.candidate_id) ? 'Added for review' : 'Add to highlight review'}</button></article>)}
      </>}
    </>}
    <p className="fineprint">Model: Gabriele Giudici, E-BARD basketball object detection (CC BY 4.0); RF-DETR architecture (Apache-2.0). Experimental observations.</p>
  </section>;
}
