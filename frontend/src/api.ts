// Service layer: the only module that knows backend routes.
import type {
  Clip,
  Envelope,
  Game,
  GameEvent,
  HighlightBundle,
  Items,
  Job,
  ReviewRequest,
  RosterGroup,
  TrackSample,
  ShotReviewDecision,
  ShotSegments,
} from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function detailMessage(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d)))
        .join('; ');
    }
  }
  if (status === 0) return 'Unable to connect. Please try again.';
  return `Request failed (HTTP ${status})`;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error;
    throw new ApiError(0, detailMessage(null, 0));
  }
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!response.ok) throw new ApiError(response.status, detailMessage(body, response.status));
  return body as T;
}

const enc = encodeURIComponent;

export const api = {
  removeGame: (id: string) => request(`/api/games/${enc(id)}`, { method: 'DELETE' }),
  restoreGame: (id: string) => request<Game>(`/api/games/${enc(id)}/restore`, { method: 'POST' }),
  libraryStatus: (signal?: AbortSignal) => request<Items<{ game_id: string; status: Job['status']; phase: string | null }>>('/api/library/status', { signal }).then(r => r.items),
  listGames: (signal?: AbortSignal) => request<Items<Game>>('/api/games', { signal }).then((r) => r.items),

  loadSample: () => request<Game>('/api/games/sample?auto_analyze=true', { method: 'POST' }),

  /** Multipart upload with progress (fetch cannot report upload progress). */
  uploadGame(file: File, rightsConfirmed: boolean, onProgress?: (fraction: number) => void): Promise<Game> {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append('video', file);
      form.append('rights_confirmed', rightsConfirmed ? 'true' : 'false');
      form.append('sport', 'basketball');
      form.append('auto_analyze', 'true');
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/games');
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress?.(e.loaded / e.total);
      };
      xhr.onerror = () => reject(new ApiError(0, detailMessage(null, 0)));
      xhr.onload = () => {
        let body: unknown = null;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          body = null;
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body as Game);
        else reject(new ApiError(xhr.status, detailMessage(body, xhr.status)));
      };
      xhr.send(form);
    });
  },

  analyze: (gameId: string, idempotencyKey: string) =>
    request<Job>(`/api/games/${enc(gameId)}/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ stride: 5, confidence: 0.25, replay_paced: false }),
    }),

  listJobs: (gameId: string, signal?: AbortSignal) =>
    request<Items<Job>>(`/api/games/${enc(gameId)}/jobs`, { signal }).then((r) => r.items),

  cancelAnalysis: (jobId: string, signal?: AbortSignal) =>
    request<Job>(`/api/jobs/${enc(jobId)}/cancel`, { method: 'POST', signal }),

  getJob: (jobId: string, signal?: AbortSignal) => request<Job>(`/api/jobs/${enc(jobId)}`, { signal }),

  tracks: (gameId: string, signal?: AbortSignal) =>
    request<Items<TrackSample>>(`/api/games/${enc(gameId)}/tracks`, { signal }).then((r) => r.items),

  clips: (gameId: string, signal?: AbortSignal) =>
    request<Items<Clip>>(`/api/games/${enc(gameId)}/clips`, { signal }).then((r) => r.items),

  events: (gameId: string, signal?: AbortSignal) =>
    request<Items<GameEvent>>(`/api/games/${enc(gameId)}/events`, { signal }).then((r) => r.items),

  shots: (gameId: string, signal?: AbortSignal) =>
    request<ShotSegments>(`/api/games/${enc(gameId)}/shots`, { signal }),

  createShotClip: (gameId: string, shotId: string, jobId: string, signal?: AbortSignal) =>
    request<Clip>(`/api/games/${enc(gameId)}/shots/${enc(shotId)}/clip`, {
      method: 'POST', signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: jobId }),
    }),

  reviewShot: (gameId: string, shotId: string, jobId: string, decision: ShotReviewDecision, signal?: AbortSignal) =>
    request<ShotSegments>(`/api/games/${enc(gameId)}/shots/${enc(shotId)}/review`, {
      method: 'PUT', signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, job_id: jobId }),
    }),

  highlights: (gameId: string, signal?: AbortSignal) =>
    request<HighlightBundle>(`/api/games/${enc(gameId)}/highlights`, { signal }),

  /** Returns 202 + bundle. Reuses latest successful analysis tracks; no inference rerun. */
  generateHighlights: (gameId: string, body: { max_clips: number; top_per_player: number }, idempotencyKey: string) =>
    request<HighlightBundle>(`/api/games/${enc(gameId)}/highlights`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(body),
    }),

  reviewHighlight: (gameId: string, candidateId: string, body: ReviewRequest) =>
    request<HighlightBundle>(`/api/games/${enc(gameId)}/highlights/candidates/${enc(candidateId)}/review`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  /** Full replacement of reviewer-confirmed groups. */
  setHighlightPlayers: (gameId: string, players: RosterGroup[]) =>
    request<HighlightBundle>(`/api/games/${enc(gameId)}/highlights/players`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ players }),
    }),

  /** Opens the replayable SSE stream. Caller must call close(). */
  stream(
    gameId: string,
    handlers: { onEnvelope: (e: Envelope, receivedAtMs: number) => void; onOpen: () => void; onError: () => void },
  ): EventSource {
    const source = new EventSource(`/api/games/${enc(gameId)}/stream`);
    source.onopen = handlers.onOpen;
    source.onerror = handlers.onError;
    source.onmessage = (message) => {
      const receivedAt = Date.now();
      try {
        handlers.onEnvelope(JSON.parse(message.data) as Envelope, receivedAt);
      } catch {
        // Malformed envelope: skip rather than corrupt state.
      }
    };
    return source;
  },
};
