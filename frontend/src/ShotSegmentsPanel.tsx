import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { formatMs } from './FilmPlayer';
import type { CameraShot, Clip, JobStatus, ShotReviewDecision, ShotSegments } from './types';

type Props = {
  gameId: string;
  jobId: string | null;
  jobStatus?: JobStatus;
  analysisActive: boolean;
  refreshKey?: number;
  onSeek: (ms: number) => void;
  onUpdated: () => void;
  onShots?: (shots: CameraShot[]) => void;
};
const PAGE_SIZE = 10;
const LABEL: Record<CameraShot['category'], string> = {
  gameplay_candidate: 'Likely gameplay',
  non_gameplay_candidate: 'Likely non-gameplay',
  uncertain: 'Uncertain',
};
const included = (shot: CameraShot) => shot.review_status === 'confirmed_gameplay'
  || (shot.review_status !== 'confirmed_non_gameplay' && shot.category === 'gameplay_candidate');
const errorText = (error: unknown) => error instanceof ApiError ? error.message : 'Could not update camera shots. Please try again.';

export function ShotSegmentsPanel(props: Props) {
  return <ShotSegmentsInner key={`${props.gameId}:${props.jobId ?? ''}`} {...props} />;
}

function ShotSegmentsInner({ gameId, jobId, jobStatus, analysisActive, refreshKey, onSeek, onUpdated, onShots }: Props) {
  const [shots, setShots] = useState<CameraShot[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [busy, setBusy] = useState<string | null>(null);
  const [operation, setOperation] = useState<'review' | 'export' | null>(null);
  const [exports, setExports] = useState<Record<string, Clip>>({});
  const [retry, setRetry] = useState(0);
  const mutation = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const usable = !!jobId && jobStatus === 'succeeded' && !analysisActive;
  const canRead = !!jobId && (jobStatus === 'queued' || jobStatus === 'running' || jobStatus === 'succeeded');

  useEffect(() => {
    setShots([]);
    onShots?.([]);
    setNotice('');
    setExports({});
    setLimit(PAGE_SIZE);
  }, [gameId, jobId, onShots]);

  useEffect(() => {
    const controller = new AbortController();
    const version = ++generation.current;
    let timer: number | undefined;
    mutation.current?.abort();
    setBusy(null);
    setOperation(null);
    setError('');
    if (!canRead) {
      setShots([]);
      setExports({});
      onShots?.([]);
      setLoading(false);
      return () => controller.abort();
    }
    const load = async () => {
      setLoading(true);
      try {
        const result = await api.shots(gameId, controller.signal);
        if (controller.signal.aborted || generation.current !== version) return;
        if (result.job_id !== jobId) {
          setShots([]);
          onShots?.([]);
          if (usable) setError('These camera shots belong to an older analysis. Refresh to load the current result.');
          return;
        }
        setShots(result.items);
        onShots?.(result.items);
        setError('');
      } catch (err) {
        if (!controller.signal.aborted && generation.current === version) setError(errorText(err));
      } finally {
        if (!controller.signal.aborted && generation.current === version) {
          setLoading(false);
          if (analysisActive) timer = window.setTimeout(() => void load(), 1500);
        }
      }
    };
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [gameId, jobId, canRead, usable, analysisActive, refreshKey, retry, onShots]);

  useEffect(() => () => mutation.current?.abort(), []);

  const review = async (shot: CameraShot, decision: ShotReviewDecision) => {
    if (!usable || !jobId || busy) return;
    mutation.current?.abort();
    const controller = new AbortController();
    mutation.current = controller;
    const version = generation.current;
    setBusy(shot.shot_id);
    setOperation('review');
    setError('');
    setNotice('');
    try {
      const result = await api.reviewShot(gameId, shot.shot_id, jobId, decision, controller.signal);
      if (controller.signal.aborted || generation.current !== version) return;
      if (result.job_id !== jobId) throw new ApiError(409, 'The analysis changed. Reload the current camera shots before reviewing.');
      setShots(result.items);
      onShots?.(result.items);
      setNotice('Shot review saved. Previous highlight picks were cleared; generate highlights again to apply this selection.');
      onUpdated();
    } catch (err) {
      if (!controller.signal.aborted && generation.current === version) setError(errorText(err));
    } finally {
      if (!controller.signal.aborted && generation.current === version) { setBusy(null); setOperation(null); }
    }
  };

  const createClip = async (shot: CameraShot) => {
    if (!usable || !jobId || busy) return;
    mutation.current?.abort();
    const controller = new AbortController();
    mutation.current = controller;
    const version = generation.current;
    setBusy(shot.shot_id);
    setOperation('export');
    setError('');
    setNotice('');
    try {
      const clip = await api.createShotClip(gameId, shot.shot_id, jobId, controller.signal);
      if (controller.signal.aborted || generation.current !== version) return;
      if ((clip.job_id && clip.job_id !== jobId) || clip.source_start_ms !== shot.start_ms || clip.source_end_ms !== shot.end_ms) {
        throw new ApiError(409, 'The shot changed. Reload its source range before exporting.');
      }
      setExports(current => ({ ...current, [shot.shot_id]: clip }));
      setNotice('Shot clip ready. This source review clip does not add player statistics.');
      onUpdated();
    } catch (err) {
      if (!controller.signal.aborted && generation.current === version) setError(errorText(err));
    } finally {
      if (!controller.signal.aborted && generation.current === version) { setBusy(null); setOperation(null); }
    }
  };

  return <section className="panel camera-shots" aria-label="Camera shot review">
    <div className="panel-head"><h3>Camera Shots</h3>{usable && <span className="muted small">{shots.length} shot{shots.length === 1 ? '' : 's'} · {shots.filter(included).length} included</span>}</div>
    <p className="muted small">Viewpoint changes divide the footage into separate shots. Labels are suggestions. Likely non-gameplay and uncertain shots stay out of automatic highlights until you confirm gameplay; the original footage remains available.</p>
    {error && <div className="alert error" role="alert">{error} <button className="link" disabled={busy !== null} onClick={() => setRetry(n => n + 1)}>Retry</button></div>}
    {notice && <p className="alert warn" role="status">{notice}</p>}
    {analysisActive ? <p className="muted small">Detecting camera shots. Review becomes available when analysis finishes.</p>
      : !usable ? <p className="muted small">Run analysis to detect and review camera shots.</p>
      : loading ? <p className="muted small">Loading camera shots…</p>
      : !shots.length ? <p className="muted small">No shot segmentation is available for this run. Re-run analysis to detect camera changes.</p>
      : <>
        <ol className="hl-cands">
          {shots.slice(0, limit).map((shot, index) => <li key={shot.shot_id} className="camera-shot">
            <div className="hl-cand-head"><strong>Shot {index + 1}</strong><span className="hl-badge">{LABEL[shot.category]} · suggested</span></div>
            <p className="range">{formatMs(shot.start_ms)} → {formatMs(shot.end_ms)}</p>
            <p className="small">{shot.review_status === 'confirmed_gameplay' ? 'Gameplay confirmed · included'
              : shot.review_status === 'confirmed_non_gameplay' ? 'Excluded by reviewer'
              : included(shot) ? 'Included provisionally · review suggested' : 'Needs review · excluded from automatic highlights'}</p>
            {!!shot.reasons?.length && <p className="fineprint">{shot.reasons.map(reason => reason.replaceAll('_', ' ')).join(' · ')}</p>}
            <div className="hl-cand-actions">
              <button className="secondary small" onClick={() => onSeek(shot.start_ms)}>View Source</button>
              {exports[shot.shot_id] ? <a href={exports[shot.shot_id].uri} target="_blank" rel="noreferrer">Open Shot Clip ↗</a>
                : <button className="secondary small" disabled={!usable || busy !== null} onClick={() => void createClip(shot)}>{busy === shot.shot_id && operation === 'export' ? 'Creating Clip…' : 'Create Shot Clip'}</button>}
              <button className="small" disabled={!usable || busy !== null || shot.review_status === 'confirmed_gameplay'} onClick={() => void review(shot, 'gameplay')}>{busy === shot.shot_id && operation === 'review' ? 'Saving…' : 'Confirm Gameplay'}</button>
              <button className="ghost small danger" disabled={!usable || busy !== null || shot.review_status === 'confirmed_non_gameplay'} onClick={() => void review(shot, 'non_gameplay')}>Exclude Shot</button>
              <button className="ghost small" disabled={!usable || busy !== null || shot.review_status === 'pending'} onClick={() => void review(shot, 'pending')}>Reset Review</button>
            </div>
          </li>)}
        </ol>
        {shots.length > limit && <button className="secondary small" onClick={() => setLimit(n => n + PAGE_SIZE)}>Show More Shots ({shots.length - limit} Remaining)</button>}
      </>}
  </section>;
}
