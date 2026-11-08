import { useEffect, useMemo, useState, type RefObject } from 'react';
import type { BallObject } from './BasketballPanel';
import type { CameraShot, Clip, Game, Keypoint, TrackSample } from './types';

/** Overlay only shows a sampled frame if it is at most this far before the playhead. */
export const SAMPLE_WINDOW_MS = 400;

// COCO-17 skeleton edges, by keypoint name.
const SKELETON: [string, string][] = [
  ['left_ankle', 'left_knee'], ['left_knee', 'left_hip'], ['right_ankle', 'right_knee'], ['right_knee', 'right_hip'],
  ['left_hip', 'right_hip'], ['left_shoulder', 'left_hip'], ['right_shoulder', 'right_hip'],
  ['left_shoulder', 'right_shoulder'], ['left_shoulder', 'left_elbow'], ['right_shoulder', 'right_elbow'],
  ['left_elbow', 'left_wrist'], ['right_elbow', 'right_wrist'], ['left_eye', 'right_eye'], ['nose', 'left_eye'],
  ['nose', 'right_eye'], ['left_eye', 'left_ear'], ['right_eye', 'right_ear'], ['left_ear', 'left_shoulder'],
  ['right_ear', 'right_shoulder'],
];

export type TrackSummary = { playerLabel?: string; id: string; count: number; first_ms: number; last_ms: number };

export type FrameIndex = {
  times: number[];
  byTime: Map<number, TrackSample[]>;
  trackIds: TrackSummary[];
};

export function buildFrameIndex(tracks: Map<string, TrackSample>): FrameIndex {
  const byTime = new Map<number, TrackSample[]>();
  const summary = new Map<string, TrackSummary>();
  for (const s of tracks.values()) {
    const list = byTime.get(s.timestamp_ms);
    if (list) list.push(s);
    else byTime.set(s.timestamp_ms, [s]);
    const t = summary.get(s.track_id);
    if (t) {
      t.count++;
      t.first_ms = Math.min(t.first_ms, s.timestamp_ms);
      t.last_ms = Math.max(t.last_ms, s.timestamp_ms);
    } else summary.set(s.track_id, { playerLabel: s.player_label, id: s.track_id, count: 1, first_ms: s.timestamp_ms, last_ms: s.timestamp_ms });
  }
  const times = [...byTime.keys()].sort((a, b) => a - b);
  const trackIds = [...summary.values()].sort((a, b) => b.count - a.count || a.id.localeCompare(b.id, undefined, { numeric: true }));
  return { times, byTime, trackIds };
}

/** Nearest sampled timestamp at or before t, if within the window. No interpolation. */
export function nearestPriorSample(times: number[], tMs: number): number | null {
  let lo = 0;
  let hi = times.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= tMs) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  if (found < 0) return null;
  return tMs - times[found] <= SAMPLE_WINDOW_MS ? times[found] : null;
}

export type OverlayFrame = {
  samples: TrackSample[];
  /** Model sample time shown (the prior sample when interpolating). */
  sampleTime: number | null;
  /** Next model sample used for interpolation, if any. */
  nextTime: number | null;
};

const lerp = (a: number, b: number, f: number) => a + (b - a) * f;

function lerpSample(a: TrackSample, b: TrackSample, f: number): TrackSample {
  const bk = new Map(b.keypoints.map((k) => [k.name, k]));
  const keypoints: Keypoint[] = a.keypoints.map((k) => {
    const q = bk.get(k.name);
    if (!q || k.visibility <= 0 || q.visibility <= 0) return f < 0.5 || !q ? k : q;
    return { name: k.name, x: lerp(k.x, q.x, f), y: lerp(k.y, q.y, f), visibility: Math.min(k.visibility, q.visibility) };
  });
  const bbox = a.bbox.map((v, i) => lerp(v, b.bbox[i], f)) as TrackSample['bbox'];
  return { ...a, bbox, keypoints, confidence: Math.min(a.confidence, b.confidence) };
}

/**
 * Track samples to draw at the playhead. With `interpolate`, each track's box and
 * pose are linearly blended between the model samples bracketing the playhead
 * (display smoothing only; nothing new is inferred). Otherwise the prior sample is held.
 */
export function overlayAt(index: FrameIndex, tMs: number, interpolate: boolean, allow: (t: number) => boolean = () => true): OverlayFrame {
  const sampleTime = nearestPriorSample(index.times, tMs);
  if (sampleTime === null || !allow(sampleTime)) return { samples: [], sampleTime: null, nextTime: null };
  const prior = index.byTime.get(sampleTime) ?? [];
  const pos = index.times.indexOf(sampleTime);
  const nextTime = index.times[pos + 1];
  if (!interpolate || sampleTime === tMs || nextTime === undefined || nextTime - sampleTime > SAMPLE_WINDOW_MS || !allow(nextTime)) {
    return { samples: prior, sampleTime, nextTime: null };
  }
  const next = new Map((index.byTime.get(nextTime) ?? []).map((s) => [s.track_id, s]));
  const f = (tMs - sampleTime) / (nextTime - sampleTime);
  const samples = prior.map((s) => {
    const n = next.get(s.track_id);
    return n ? lerpSample(s, n, f) : s;
  });
  return { samples, sampleTime, nextTime };
}

export function trackColor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return `hsl(${(h * 47) % 360} 85% 60%)`;
}

export function formatMs(ms: number): string {
  if (!Number.isFinite(ms)) return '–';
  const total = Math.max(0, ms) / 1000;
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, '0')}`;
}

/** Tracks video.currentTime at display rate while playing. */
export function usePlayhead(videoRef: RefObject<HTMLVideoElement | null>): number {
  const [ms, setMs] = useState(0);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    // Prefer requestVideoFrameCallback: its mediaTime is the timestamp of the frame
    // actually composited, so the overlay matches what is on screen. currentTime
    // read on requestAnimationFrame can run a frame or more ahead/behind.
    const rvfc = typeof video.requestVideoFrameCallback === 'function';
    let frame = 0;
    const read = () => setMs(Math.round(video.currentTime * 1000));
    const cancel = () => {
      if (rvfc) video.cancelVideoFrameCallback(frame);
      else cancelAnimationFrame(frame);
    };
    const loop = () => {
      read();
      frame = requestAnimationFrame(loop);
    };
    const onFrame = (_now: number, meta: VideoFrameCallbackMetadata) => {
      setMs(Math.round(meta.mediaTime * 1000));
      frame = video.requestVideoFrameCallback(onFrame);
    };
    const play = () => {
      cancel();
      frame = rvfc ? video.requestVideoFrameCallback(onFrame) : requestAnimationFrame(loop);
    };
    const stop = () => {
      cancel();
      read();
    };
    video.addEventListener('play', play);
    video.addEventListener('pause', stop);
    video.addEventListener('ended', stop);
    video.addEventListener('seeked', read);
    if (!rvfc) video.addEventListener('timeupdate', read);
    video.addEventListener('loadedmetadata', read);
    if (!video.paused) play();
    return () => {
      cancel();
      video.removeEventListener('play', play);
      video.removeEventListener('pause', stop);
      video.removeEventListener('ended', stop);
      video.removeEventListener('seeked', read);
      video.removeEventListener('timeupdate', read);
      video.removeEventListener('loadedmetadata', read);
    };
  }, [videoRef]);
  return ms;
}

type PlayerProps = {
  game: Game;
  videoRef: RefObject<HTMLVideoElement | null>;
  playheadMs: number;
  index: FrameIndex;
  showBoxes: boolean;
  showPose: boolean;
  showFootDot: boolean;
  interpolate: boolean;
  objects?: BallObject[];
  shots?: CameraShot[];
  selectedTrack: string | null;
};

export function FilmPlayer({ game, videoRef, playheadMs, index, showBoxes, showPose, showFootDot, interpolate, selectedTrack, objects = [], shots = [] }: PlayerProps) {
  const [videoError, setVideoError] = useState<string | null>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const W = game.width || natural?.w || 16;
  const H = game.height || natural?.h || 9;
  const currentShot = shots.find(s => s.start_ms <= playheadMs && playheadMs < s.end_ms);
  const inCurrentShot = (t: number | null) => t !== null && (!shots.length || !!currentShot && currentShot.start_ms <= t && t < currentShot.end_ms);
  // Never draw, or interpolate toward, a sample from a different camera shot.
  const { samples, sampleTime, nextTime } = useMemo(
    () => overlayAt(index, playheadMs, interpolate, (t) => inCurrentShot(t)),
    [index, playheadMs, interpolate, currentShot?.start_ms, currentShot?.end_ms, shots.length],
  );
  const visible = selectedTrack ? samples.filter((s) => s.track_id === selectedTrack) : samples;
  const objectIndex = useMemo(() => { const times = [...new Set(objects.map(o => o.timestamp_ms))].sort((a,b) => a-b); const byTime = new Map<number, BallObject[]>(); for (const o of objects) byTime.set(o.timestamp_ms, [...(byTime.get(o.timestamp_ms) ?? []), o]); return { times, byTime }; }, [objects]);
  const objectTime = nearestPriorSample(objectIndex.times, playheadMs);
  const visibleObjects = objectTime !== null && inCurrentShot(objectTime) && playheadMs - objectTime <= 200 ? objectIndex.byTime.get(objectTime) ?? [] : [];
  const mismatch = natural && game.width && Math.abs(natural.w / natural.h - game.width / game.height) > 0.01;
  const font = H * 0.024;

  return (
    <div className="player">
      <div className="stage" style={{ aspectRatio: `${W} / ${H}`, width: `min(100%, calc(68vh * ${W / H}))` }}>
        <video
          ref={videoRef}
          src={game.source_uri}
          controls
          playsInline
          preload="metadata"
          onLoadedMetadata={(e) => {
            setVideoError(null);
            setNatural({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight });
          }}
          onError={() => setVideoError('The video could not be loaded. Please try again.')}
        />
        <svg className="overlay" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
          {visibleObjects.map((o, i) => { const [x,y,w,h] = o.bbox; const color = o.class_name === 'basketball' ? '#ffe45c' : '#56e7ff'; return <g key={`object-${i}`}><rect x={x*W} y={y*H} width={w*W} height={h*H} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke"/><text x={x*W} y={Math.max(font,y*H-4)} fill={color} fontSize={font}>{o.class_name}?</text></g>; })}
          {visible.map((s) => {
            const color = trackColor(s.player_id ?? s.track_id);
            const tags = [
              s.team_label === 'Team A' ? 'A?' : s.team_label === 'Team B' ? 'B?' : null,
              s.role_status === 'likely_non_player' ? 'non-player?' : s.role_status === 'review_required' ? 'role?' : null,
            ].filter(Boolean);
            const flagged = s.role_status === 'likely_non_player' || s.role_status === 'review_required';
            const label = `${s.player_label ?? `T${s.track_id}`}${tags.length ? ` ${tags.join(' ')}` : ''}`;
            const [x, y, w, h] = s.bbox;
            const pts = new Map(s.keypoints.map((k) => [k.name, k]));
            return (
              <g key={s.track_id}>
                {showBoxes && (
                  <>
                    <rect x={x * W} y={y * H} width={w * W} height={h * H} fill="none" stroke={color} strokeWidth={2} strokeDasharray={flagged ? '6 4' : undefined} vectorEffect="non-scaling-stroke" />
                    <rect x={x * W} y={y * H - font * 1.3} width={font * 0.62 * (`${label} ${s.confidence.toFixed(2)}`.length + 1)} height={font * 1.3} fill={color} opacity={0.85} />
                    <text x={x * W + font * 0.3} y={y * H - font * 0.35} fontSize={font} fill="#0b0d10" fontWeight={700}>
                      {label} {s.confidence.toFixed(2)}
                    </text>
                  </>
                )}
                {showPose && (
                  <>
                    {SKELETON.map(([a, b]) => {
                      const p = pts.get(a);
                      const q = pts.get(b);
                      if (!p || !q || p.visibility <= 0 || q.visibility <= 0) return null;
                      return <line key={a + b} x1={p.x * W} y1={p.y * H} x2={q.x * W} y2={q.y * H} stroke={color} strokeWidth={2} strokeOpacity={0.9} vectorEffect="non-scaling-stroke" />;
                    })}
                    {s.keypoints.map((k) =>
                      k.visibility > 0 ? <circle key={k.name} cx={k.x * W} cy={k.y * H} r={H * 0.004} fill="#fff" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" /> : null,
                    )}
                  </>
                )}
                {showFootDot && (
                  // Ground point: bottom-center of the box, roughly where the player stands.
                  <circle cx={(x + w / 2) * W} cy={(y + h) * H} r={H * 0.007} fill={color} stroke="#0b0d10" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
                )}
              </g>
            );
          })}
        </svg>
        <div className="stage-badge">
          Model predictions · sampled frames
          <span>
            {sampleTime === null
              ? index.times.length
                ? `no sample within ${SAMPLE_WINDOW_MS} ms before playhead`
                : 'no track samples yet'
              : `${
                  nextTime !== null
                    ? `interpolated ${formatMs(sampleTime)}→${formatMs(nextTime)}`
                    : `sample @ ${formatMs(sampleTime)} (−${playheadMs - sampleTime} ms)`
                } · ${visible.length} box${visible.length === 1 ? '' : 'es'}`}
          </span>
        </div>
        {videoError && <div className="stage-error">{videoError}</div>}
      </div>
      {mismatch && (
        <p className="warn small">
          Decoded video is {natural.w}×{natural.h} but metadata says {game.width}×{game.height}; overlay alignment may be off.
        </p>
      )}
    </div>
  );
}

type TimelineProps = {
  durationMs: number;
  playheadMs: number;
  index: FrameIndex;
  clips: Clip[];
  selectedTrack: string | null;
  onSeek: (ms: number) => void;
};

export function Timeline({ durationMs, playheadMs, index, clips, selectedTrack, onSeek }: TimelineProps) {
  const pct = (ms: number) => `${Math.min(100, Math.max(0, (ms / Math.max(1, durationMs)) * 100))}%`;
  const ticks = useMemo(() => {
    // Downsample to at most ~400 ticks so long videos stay cheap to render.
    const step = Math.max(1, Math.ceil(index.times.length / 400));
    const out: { t: number; n: number }[] = [];
    for (let i = 0; i < index.times.length; i += step) {
      const t = index.times[i];
      const list = index.byTime.get(t) ?? [];
      const n = selectedTrack ? list.filter((s) => s.track_id === selectedTrack).length : list.length;
      if (n) out.push({ t, n });
    }
    return out;
  }, [index, selectedTrack]);
  const maxN = Math.max(1, ...ticks.map((t) => t.n));

  return (
    <div
      className="timeline"
      role="slider"
      tabIndex={0}
      aria-label="Source timeline"
      aria-valuemin={0}
      aria-valuemax={durationMs}
      aria-valuenow={playheadMs}
      onClick={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        onSeek(((e.clientX - rect.left) / rect.width) * durationMs);
      }}
      onKeyDown={(e) => {
        if (e.key === 'ArrowRight') onSeek(playheadMs + 1000);
        if (e.key === 'ArrowLeft') onSeek(playheadMs - 1000);
      }}
    >
      <div className="lane lane-clips">
        {clips.map((c, i) => (
          <div key={c.clip_id} className="seg" style={{ left: pct(c.source_start_ms), width: `calc(${pct(c.source_end_ms - c.source_start_ms)} - 2px)` }} title={`Clip ${i + 1}: ${formatMs(c.source_start_ms)}–${formatMs(c.source_end_ms)}`}>
            Clip {i + 1}
          </div>
        ))}
      </div>
      <div className="lane lane-ticks">
        {ticks.map(({ t, n }) => (
          <span key={t} style={{ left: pct(t), height: `${20 + (n / maxN) * 80}%` }} />
        ))}
      </div>
      <div className="playhead" style={{ left: pct(playheadMs) }} />
      <div className="timeline-scale">
        <span>0:00</span>
        <span>{formatMs(durationMs)}</span>
      </div>
    </div>
  );
}
