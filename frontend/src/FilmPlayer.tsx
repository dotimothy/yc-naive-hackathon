import { useEffect, useMemo, useState, type RefObject } from 'react';
import type { Clip, Game, TrackSample } from './types';

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
    let frame = 0;
    const read = () => setMs(Math.round(video.currentTime * 1000));
    const loop = () => {
      read();
      frame = requestAnimationFrame(loop);
    };
    const play = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(loop);
    };
    const stop = () => {
      cancelAnimationFrame(frame);
      read();
    };
    video.addEventListener('play', play);
    video.addEventListener('pause', stop);
    video.addEventListener('ended', stop);
    video.addEventListener('seeked', read);
    video.addEventListener('timeupdate', read);
    video.addEventListener('loadedmetadata', read);
    if (!video.paused) play();
    return () => {
      cancelAnimationFrame(frame);
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
  selectedTrack: string | null;
};

export function FilmPlayer({ game, videoRef, playheadMs, index, showBoxes, showPose, selectedTrack }: PlayerProps) {
  const [videoError, setVideoError] = useState<string | null>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const W = game.width || natural?.w || 16;
  const H = game.height || natural?.h || 9;
  const sampleTime = nearestPriorSample(index.times, playheadMs);
  const samples = sampleTime === null ? [] : index.byTime.get(sampleTime) ?? [];
  const visible = selectedTrack ? samples.filter((s) => s.track_id === selectedTrack) : samples;
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
          onError={() => setVideoError('Source video could not be loaded from the local API.')}
        />
        <svg className="overlay" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
          {visible.map((s) => {
            const color = trackColor(s.player_id ?? s.track_id);
            const label = s.player_label ?? `T${s.track_id}`;
            const [x, y, w, h] = s.bbox;
            const pts = new Map(s.keypoints.map((k) => [k.name, k]));
            return (
              <g key={s.track_id}>
                {showBoxes && (
                  <>
                    <rect x={x * W} y={y * H} width={w * W} height={h * H} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" />
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
              : `sample @ ${formatMs(sampleTime)} (−${playheadMs - sampleTime} ms) · ${visible.length} box${visible.length === 1 ? '' : 'es'}`}
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
