import { request } from './api';
import { useCallback, useEffect, useState } from 'react';
import { formatMs } from './FilmPlayer';

export type BallObject = { timestamp_ms: number; class_name: string; bbox: [number, number, number, number]; confidence: number };
type Proposal = { candidate_id: string; start_ms: number; end_ms: number; approach_ms: number; evidence_quality: number };
type BallRun = { identity_stale?: boolean; run_id: string | null; status: string; parent_job_id: string | null; error?: string; report?: { processed_frames: number; frames_with_ball: number; stable_hoops: unknown[]; shot_candidates: Proposal[]; device: string; elapsed_seconds: number; coverage_scope?: string; analyzed_until_ms?: number } };
export function BasketballPanel({ gameId, jobId, analysisActive, tracks, onSeek, onObjects, onImported, refreshKey }: {
  refreshKey?: number; gameId: string; jobId: string | null; analysisActive: boolean; tracks: { id: string; playerLabel?: string }[];
  onSeek: (ms: number) => void; onObjects: (objects: BallObject[]) => void; onImported: () => void;
}) {
  const path = `/api/games/${gameId}/basketball`;
  const [run, setRun] = useState<BallRun | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [actor, setActor] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [imported, setImported] = useState<string[]>([]);
  const [key, setKey] = useState(() => crypto.randomUUID());
  const load = useCallback(async () => {
    try { setRun(await request<BallRun>(path)); setError(''); }
    catch (e) { setError((e as Error).message); }
  }, [path]);
  useEffect(() => { void load(); }, [load, refreshKey]);
  const active = run?.status === 'queued' || run?.status === 'running';
  useEffect(() => { if (!active) return; const timer = window.setInterval(() => void load(), 1500); return () => clearInterval(timer); }, [active, load]);
  useEffect(() => {
    let cancelled = false;
    onObjects([]);
    if (run?.status === 'succeeded') request<{items: BallObject[]}>(`${path}/objects`).then(data => { if (!cancelled) onObjects(data.items); }).catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [path, run?.run_id, run?.status, onObjects]);
  const start = async () => {
    setBusy(true); setError('');
    try { setRun(await request<BallRun>(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: JSON.stringify({ max_seconds: 60 }) })); setKey(crypto.randomUUID()); setImported([]); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const add = async (candidate: Proposal) => {
    setBusy(true); setError('');
    try { await request(`${path}/candidates/${candidate.candidate_id}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ track_id: actor, actor_confirmed: confirmed }) }); setImported(ids => [...ids, candidate.candidate_id]); onImported(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const report = run?.report;
  const stale = run?.identity_stale || (!!run?.parent_job_id && run.parent_job_id !== jobId);
  return <section className="panel basketball-panel">
    <div className="panel-head"><h3>Ball & Hoop Evidence</h3><button onClick={() => void start()} disabled={busy || active || analysisActive}>{active ? 'Detecting ball & hoop…' : 'Analyze Ball & Hoop'}</button></div>
    <p className="muted small">Local GPU detector · analyzes up to the first 60 seconds. Trajectories propose plays for review; outcomes need confirmation.</p>
    {error && <p className="alert error">{error}</p>}
    {run?.error && <p className="alert error">{run.error}</p>}
    {stale && <p className="alert warn">Player tracks have changed. Re-run ball/hoop analysis before assigning plays.</p>}
    {!report ? <p className="muted small">{active ? 'Processing sampled frames. Results appear when the pass finishes.' : 'No ball/hoop evidence yet.'}</p> : <>
      <dl className="metrics"><div><dt>Frames with ball detections</dt><dd>{report.frames_with_ball}/{report.processed_frames}</dd></div><div><dt>Stable hoop proposals</dt><dd>{report.stable_hoops.length}</dd></div><div><dt>Shot candidates</dt><dd>{report.shot_candidates.length}</dd></div><div><dt>Made / missed</dt><dd>Unknown / unknown</dd></div><div><dt>Device / wall time</dt><dd>{report.device} / {report.elapsed_seconds.toFixed(1)} s</dd></div></dl>
      <p className="fineprint">Detection coverage is not accuracy. Ball and hoop boxes show observations within 200 ms before the playhead; no inferred flight between detections. {report.coverage_scope === 'partial_video' ? 'Partial source analyzed.' : ''}</p>
      {report.shot_candidates.length === 0 ? <p className="muted small">No trajectories passed the proposal rules. This does not mean no shots occurred.</p> : <>
        <label className="select">Observed actor <select value={actor} onChange={e => { setActor(e.target.value); setConfirmed(false); }}><option value="">Choose a current track</option>{tracks.map(t => <option key={t.id} value={t.id}>{t.playerLabel ?? 'Player unknown'} · T{t.id}</option>)}</select></label>
        <label className="check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />I checked the source and this track performed the play.</label>
        {report.shot_candidates.map(c => <article key={c.candidate_id} className="ball-candidate"><span>{formatMs(c.start_ms)}–{formatMs(c.end_ms)} · outcome unverified</span><button className="secondary small" onClick={() => onSeek(c.start_ms)}>Seek source</button><button className="small" disabled={busy || stale || !actor || !confirmed || !jobId || imported.includes(c.candidate_id)} onClick={() => void add(c)}>{imported.includes(c.candidate_id) ? 'Added for review' : 'Add to highlight review'}</button></article>)}
      </>}
    </>}
    <p className="fineprint">Model: Gabriele Giudici, E-BARD basketball object detection (CC BY 4.0); RF-DETR architecture (Apache-2.0). Experimental observations.</p>
  </section>;
}
