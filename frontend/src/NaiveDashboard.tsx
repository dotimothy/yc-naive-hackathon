import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import './NaiveDashboard.css';
import { VideoThumbnail } from './VideoThumbnail';
import { normalizeActivity, TeamActivityPanel, type TeamActivity } from './NaiveTeamActivity';

/* Live pipeline dashboard with explicit controls. */
const ENDPOINT = '/naive-api/pipeline';
const PAGE_SIZE = 15;
const POLL_MS = 4000;
const TIMEOUT_MS = 3500;
/** Snapshot older than this (by its own updated_at) is flagged stale even when the request succeeds. */
const STALE_SNAPSHOT_MS = 30_000;

type BatchItem = {
  id: string; name?: string; source_url?: string; status?: string; stage?: string;
  game_id?: string; job_id?: string; error?: string; preview_url?: string; post_url?: string;
  completed_at?: string | number; camera_evidence?: string;
};
type Batch = {
  id?: string; status?: string; stop_at?: string | number; updated_at?: string | number;
  total?: number; counts?: Record<string, number>; items: BatchItem[]; deleted_count?: number;
};
type Presentation = { preview_uri?: string; track_count?: number; quiz_count?: number; quiz_music_missing?: boolean | number };
type Publication = { status?: string; url?: string; post_id?: string; error?: string; requested_visibility?: string; profile?: string };
type Member = { session_id?: string; agent_id?: string; status?: string; stop_reason?: string; cost_micro_usd?: number };
type Workflow = {
  job_id: string; game_id?: string; state?: string; created_ms?: number; completed_at?: string | number;
  reserved?: number; cost_micro_usd?: number; output?: unknown; presentation?: Presentation; publication?: Publication;
  members?: Member[];
};
type Budget = { monthly_cap_micro_usd?: number; per_video_cap_micro_usd?: number; accounted_micro_usd?: number };
type CrewMember = { name?: string; role?: string; id?: string; session_count?: number };
type Policy = { paused_until?: string; paused_reason?: string; enabled?: boolean; platform?: string; visibility?: string; stop_at?: string | number };
type MailStatus = { worker?: string; updated_at?: string; last_success_at?: string; error_code?: number; ledger?: string; requests?: Record<string, number>; replies?: Record<string, number>; recent?: {id:string;status:string;created:number;updated:number}[] };
type Pipeline = {
  updated_at?: string | number; batch: Batch | null; workflows: Workflow[]; budget: Budget | null;
  services: Record<string, string>; crew: CrewMember[]; policy: Policy | null;
  team_activity: TeamActivity | null; agentmail: MailStatus | null;
};

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const arr = <T,>(v: unknown, keep: (x: Record<string, unknown>) => boolean): T[] =>
  Array.isArray(v) ? (v.filter((x) => obj(x) && keep(x as Record<string, unknown>)) as T[]) : [];

/** Accepts only the documented shape; returns null if nothing recognisable is present. */
function normalize(raw: unknown): Pipeline | null {
  const r = obj(raw);
  if (!r) return null;
  const known = ['batch', 'workflows', 'budget', 'services', 'crew', 'policy'].some((k) => k in r);
  if (!known) return null;
  const b = obj(r.batch);
  const services: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj(r.services) ?? {})) if (typeof v === 'string' || typeof v === 'boolean') services[k] = String(v);
  return {
    updated_at: typeof r.updated_at === 'string' || typeof r.updated_at === 'number' ? r.updated_at : undefined,
    batch: b ? { ...(b as Batch), counts: obj(b.counts) as Record<string, number> | undefined, items: arr<BatchItem>(b.items, (x) => typeof x.id === 'string') } : null,
    workflows: arr<Workflow>(r.workflows, (x) => typeof x.job_id === 'string').map((w) => ({ ...w, members: arr<Member>(w.members, () => true) })),
    budget: (obj(r.budget) as Budget | null) ?? null,
    services,
    crew: arr<CrewMember>(r.crew, () => true),
    policy: (obj(r.policy) as Policy | null) ?? null,
    team_activity: normalizeActivity(r.team_activity),
    agentmail: obj(r.agentmail) as MailStatus | null,
  };
}

type Conn = 'connecting' | 'live' | 'stale' | 'offline';

function usePipeline(active: boolean) {
  const [data, setData] = useState<Pipeline | null>(null);
  const [lastOk, setLastOk] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failures, setFailures] = useState(0);
  const [polling, setPolling] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    let ctrl: AbortController | null = null;
    const tick = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      ctrl = new AbortController();
      const t = window.setTimeout(() => ctrl?.abort(), TIMEOUT_MS);
      setPolling(true);
      try {
        const res = await fetch(ENDPOINT, { signal: ctrl.signal, headers: { Accept: 'application/json' }, cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const parsed = normalize(await res.json());
        if (!parsed) throw new Error('response did not match the pipeline contract');
        if (cancelled) return;
        setData(parsed); setLastOk(Date.now()); setError(null); setFailures(0);
      } catch (e) {
        if (cancelled) return;
        const msg = e instanceof DOMException && e.name === 'AbortError' ? `no response within ${TIMEOUT_MS / 1000} s` : e instanceof Error ? e.message : 'request failed';
        setError(msg); setFailures((n) => n + 1); // keep the last good snapshot
      } finally {
        window.clearTimeout(t);
        inFlight = false;
        if (!cancelled) { setPolling(false); timer.current = window.setTimeout(tick, document.hidden ? 15000 : POLL_MS); }
      }
    };
    void tick();
    const onVisible = () => { if (!document.hidden) { window.clearTimeout(timer.current); void tick(); } };
    document.addEventListener('visibilitychange', onVisible);
    return () => { cancelled = true; ctrl?.abort(); window.clearTimeout(timer.current); document.removeEventListener('visibilitychange', onVisible); };
  }, [active]);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);

  const snapshotAt = toMs(data?.updated_at);
  const conn: Conn = error
    ? data ? (failures >= 3 ? 'offline' : 'stale') : failures > 0 ? 'offline' : 'connecting'
    : !data ? 'connecting'
    : snapshotAt !== null && now - snapshotAt > STALE_SNAPSHOT_MS ? 'stale' : 'live';
  return { data, lastOk, error, failures, conn, now, polling, snapshotAt };
}

// ---------- formatting ----------
function toMs(v: string | number | undefined): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v < 1e12 ? v * 1000 : v; // seconds vs ms epoch
  if (typeof v === 'string' && v) { const t = Date.parse(v); return Number.isFinite(t) ? t : null; }
  return null;
}
const usd = (micro: number | undefined) => {
  if (typeof micro !== 'number' || !Number.isFinite(micro)) return '–';
  const d = micro / 1e6;
  return `$${d < 0.1 && d !== 0 ? d.toFixed(4) : d.toFixed(2)}`;
};
const ago = (ms: number | null, now: number) => {
  if (ms === null) return '–';
  const s = Math.max(0, Math.round((now - ms) / 1000));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`;
};
const clock = (ms: number | null) => (ms === null ? '–' : new Date(ms).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }));
const short = (id: string | undefined, n = 8) => (id ? id.slice(0, n) : '–');
const words = (s: string | undefined) => (s ? s.replace(/[_-]+/g, ' ') : '–');
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Status → existing `.status` colour classes from styles.css plus a few local ones. */
function statusClass(s: string | undefined): string {
  const v = (s ?? '').toLowerCase();
  if (!v) return 'none';
  if (/(cancel)/.test(v)) return 'cancelled';
  if (/(fail|error|reject|denied|blocked)/.test(v)) return 'failed';
  if (/(pend|queue|wait|reserv)/.test(v)) return 'queued';
  if (/(skip|stop|disabled|private|draft|idle|empty)/.test(v)) return 'none';
  if (/ing\b|^(run|active|live)/.test(v)) return 'running';
  if (/(succeed|complete|done|published|posted|success)|^ok$/.test(v)) return 'succeeded';
  return 'queued';
}
const Status = ({ value }: { value: string | undefined }) => <span className={`status ${statusClass(value)}`}>{value ? words(value) : '–'}</span>;

/** Only render links for http(s) URLs or same-origin absolute paths; anything else stays text. */
const safeHref = (href: string | undefined) => !!href && (/^https?:\/\//i.test(href) || (href.startsWith('/') && !href.startsWith('//')));
function SafeLink({ href, children, className }: { href: string | undefined; children: ReactNode; className?: string }) {
  if (!href) return <span className="muted">–</span>;
  return safeHref(href) ? <a className={className} href={href} target="_blank" rel="noreferrer noopener">{children}</a> : <code title="Not a browser URL">{href}</code>;
}

// ---------- stages reported by the actual queue ----------
const FLOW = [
  { key: 'discover', label: 'Check Source', raw: ['discovery'], owner: 'Local Queue', description: 'Validate the authorized clip, file size, and source hash.' },
  { key: 'select', label: 'Import Video', raw: ['raw_clipping'], owner: 'PlayLattice', description: 'Register the source video in the film room.' },
  { key: 'analyze', label: 'Analyze Video', raw: ['analysis'], owner: 'CV Workers', description: 'Detect players, track movement, and identify ball and hoop evidence.' },
  { key: 'crew', label: 'Crew Review', raw: ['crew'], owner: 'Naive Team', description: 'Coordinate source checks, clip selection, captions, and package review.' },
  { key: 'quiz', label: 'Render Portrait Quiz', raw: ['final_clipping'], owner: 'Local Renderer', description: 'Create the portrait replay using the selected quiz and music.' },
  { key: 'post', label: 'Publish To TikTok', raw: ['posting'], owner: 'Naive Social Provider', description: 'Submit the prepared post and wait for a receipt.' },
  { key: 'posted', label: 'Posted', raw: ['posted'], owner: 'TikTok', description: 'A successful publication receipt was recorded.' },
];
function stageIndex(stage: string | undefined): number | null {
  const value=(stage ?? '').toLowerCase();
  const exact=FLOW.findIndex(f => f.raw.includes(value));
  return exact >= 0 ? exact : null;
}

// ---------- batch progress buckets ----------
type BucketKey = 'posted' | 'skipped' | 'failed' | 'waiting' | 'queued' | 'other';
const BUCKETS: { key: BucketKey; label: string; hint: string; statuses?: string[] }[] = [
  { key: 'posted', label: 'Posted', hint: 'On TikTok', statuses: ['posted'] },
  { key: 'skipped', label: 'Skipped', hint: 'Not eligible to post', statuses: ['skipped'] },
  { key: 'failed', label: 'Failed', hint: 'Stopped with an error', statuses: ['failed'] },
  { key: 'waiting', label: 'Waiting', hint: 'Reported “running”: in progress or awaiting retry', statuses: ['running'] },
  { key: 'queued', label: 'Queued', hint: 'Not started yet', statuses: ['queued'] },
  { key: 'other', label: 'Other', hint: 'Any other reported status' },
];
const KNOWN_STATUSES = new Set(BUCKETS.flatMap((b) => b.statuses ?? []));
const inBucket = (status: string | undefined, key: BucketKey) => {
  const b = BUCKETS.find((x) => x.key === key);
  return b?.statuses ? b.statuses.includes(status ?? '') : !KNOWN_STATUSES.has(status ?? '');
};

/** Uses the queue's own counts when present; otherwise counts the listed items (and says so). */
function batchProgress(b: Batch) {
  const reported = b.counts && Object.values(b.counts).some((v) => typeof v === 'number' && Number.isFinite(v));
  const raw: Record<string, number> = {};
  if (reported) { for (const [k, v] of Object.entries(b.counts!)) if (typeof v === 'number' && Number.isFinite(v)) raw[k] = v; }
  else for (const i of b.items) { const k = i.status ?? 'unknown'; raw[k] = (raw[k] ?? 0) + 1; }
  const n: Record<BucketKey, number> = { posted: 0, skipped: 0, failed: 0, waiting: 0, queued: 0, other: 0 };
  for (const [k, v] of Object.entries(raw)) n[BUCKETS.find((x) => x.statuses?.includes(k))?.key ?? 'other'] += v;
  const counted = Object.values(n).reduce((a, x) => a + x, 0);
  const total = typeof b.total === 'number' && Number.isFinite(b.total) ? b.total : b.items.length;
  return { n, counted, total, source: reported ? 'queue counts' : 'listed items', unaccounted: Math.max(0, total - counted) };
}

// ---------- headline status ----------
type Tone = 'bad' | 'warn' | 'ok' | 'idle';
function postingState(p: Pipeline, now: number) {
  const b = p.batch; const pol = p.policy;
  const policyEnded = pol?.stop_at !== undefined && (toMs(pol.stop_at) ?? Infinity) <= now;
  const notes: string[] = [];
  if (pol?.enabled === false) notes.push(pol.paused_reason ? `${pol.paused_reason}${pol.paused_until ? ` · Review after ${clock(toMs(pol.paused_until))}` : ''}` : policyEnded ? `Auto-publish is off: the authorized posting window ended ${clock(toMs(pol.stop_at))}.` : 'Auto-publish policy is turned off, so nothing new is sent to TikTok.');
  if (b?.status === 'cancelled') return {tone:'idle' as Tone,pill:'Cancelled',headline:'Batch cancelled',body:'Queued work stopped. Completed results and existing posts are preserved.',notes};
  if (b?.status === 'provider_blocked') {
    return { tone: 'bad' as Tone, pill: 'Blocked', headline: 'Posting is blocked by the social provider',
      body: 'The batch reports “provider blocked”: the Naive social provider is not accepting posts, so no new TikTok posts are going out.', notes };
  }
  if (pol?.enabled === false) return { tone: 'warn' as Tone, pill: 'Paused', headline: 'Posting is paused', body: notes.shift() ?? '', notes };
  const byStatus: Record<string, [Tone, string]> = {
    running: ['ok', 'Batch is running'], queued: ['idle', 'Batch is queued to resume'], completed: ['ok', 'Batch completed'],
    budget_limited: ['warn', 'Batch stopped at the budget limit'], time_limited: ['warn', 'Batch stopped at its stop time'], stopped: ['warn', 'Batch was stopped'],
  };
  if (!b) return { tone: 'idle' as Tone, pill: pol ? (pol.enabled ? 'On' : 'Unknown') : 'Unknown', headline: 'No batch reported', body: 'The endpoint is not reporting an overnight batch.', notes };
  const [tone, headline] = byStatus[b.status ?? ''] ?? ['idle' as Tone, b.status ? `Batch status: ${words(b.status)}` : 'Batch status not reported'];
  return { tone, pill: pol?.enabled === true ? 'On' : 'Unknown', headline, body: '', notes };
}

// ---------- table controls ----------
const uniq = (vals: (string | undefined)[]) => [...new Set(vals.filter((v): v is string => !!v))].sort();

/** Search + up to two select filters + fixed-size pages. Page survives polling; resets when filters change. */
function useTableControls<T>(rows: T[], text: (r: T) => string, fields: { status: (r: T) => string | undefined; stage?: (r: T) => string | undefined }, resetKey = '') {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [stage, setStage] = useState('');
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [query, status, stage, resetKey]);
  const q = query.trim().toLowerCase();
  const filtered = useMemo(() => rows.filter((r) =>
    (!status || fields.status(r) === status) && (!stage || fields.stage?.(r) === stage) && (!q || text(r).toLowerCase().includes(q)),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ), [rows, q, status, stage]);
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const statusCounts = useMemo(() => { const m = new Map<string, number>(); for (const r of rows) { const v = fields.status(r); if (v) m.set(v, (m.get(v) ?? 0) + 1); } return m;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);
  const stageCounts = useMemo(() => { const m = new Map<string, number>(); if (fields.stage) for (const r of rows) { const v = fields.stage(r); if (v) m.set(v, (m.get(v) ?? 0) + 1); } return m;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);
  return {
    query, setQuery, status, setStatus, stage, setStage, page: current, setPage, pages, filtered,
    visible: filtered.slice(current * PAGE_SIZE, current * PAGE_SIZE + PAGE_SIZE),
    statuses: uniq([...statusCounts.keys(), status]).map((v) => [v, statusCounts.get(v) ?? 0] as const),
    stages: fields.stage ? uniq([...stageCounts.keys(), stage]).map((v) => [v, stageCounts.get(v) ?? 0] as const) : null,
    active: !!(q || status || stage),
    reset: () => { setQuery(''); setStatus(''); setStage(''); },
  };
}
type Controls = ReturnType<typeof useTableControls<unknown>>;

function TableToolbar({ id, c, placeholder, statusLabel = 'Status', stageLabel = 'Stage' }: { id: string; c: Controls; placeholder: string; statusLabel?: string; stageLabel?: string }) {
  return (
    <div className="naive-toolbar">
      <label htmlFor={`${id}-q`}><span>Search</span>
        <input id={`${id}-q`} type="search" value={c.query} placeholder={placeholder} onChange={(e) => c.setQuery(e.target.value)} />
      </label>
      <label htmlFor={`${id}-status`}><span>{statusLabel}</span>
        <select id={`${id}-status`} value={c.status} onChange={(e) => c.setStatus(e.target.value)}>
          <option value="">All ({c.statuses.reduce((n, [, k]) => n + k, 0)})</option>
          {c.statuses.map(([v, n]) => <option key={v} value={v}>{words(v)} ({n})</option>)}
        </select>
      </label>
      {c.stages && (
        <label htmlFor={`${id}-stage`}><span>{stageLabel}</span>
          <select id={`${id}-stage`} value={c.stage} onChange={(e) => c.setStage(e.target.value)}>
            <option value="">All</option>
            {c.stages.map(([v, n]) => <option key={v} value={v}>{words(v)} ({n})</option>)}
          </select>
        </label>
      )}
      {c.active && <button type="button" className="ghost small" onClick={c.reset}>Clear Filters</button>}
    </div>
  );
}

function Pager({ c, total, noun }: { c: Controls; total: number; noun: string }) {
  const n = c.filtered.length;
  return (
    <div className="table-pagination" role="navigation" aria-label={`${noun} pages`}>
      <span aria-live="polite">
        {n === 0 ? `No ${noun} match` : `${noun[0].toUpperCase()}${noun.slice(1)} ${c.page * PAGE_SIZE + 1}–${Math.min((c.page + 1) * PAGE_SIZE, n)} of ${n}`}
        {n !== total && ` (filtered from ${total})`}
      </span>
      {c.pages > 1 && <>
        <button type="button" className="secondary small" disabled={c.page === 0} onClick={() => c.setPage(0)} aria-label="First page">«</button>
        <button type="button" className="secondary small" disabled={c.page === 0} onClick={() => c.setPage(c.page - 1)}>Previous</button>
        <span className="naive-pageno">Page {c.page + 1} / {c.pages}</span>
        <button type="button" className="secondary small" disabled={c.page + 1 >= c.pages} onClick={() => c.setPage(c.page + 1)}>Next</button>
        <button type="button" className="secondary small" disabled={c.page + 1 >= c.pages} onClick={() => c.setPage(c.pages - 1)} aria-label="Last page">»</button>
      </>}
    </div>
  );
}

// ---------- view ----------
const CONN_LABEL: Record<Conn, string> = { connecting: 'Connecting', live: 'Live', stale: 'Stale', offline: 'Offline' };
type Focus = { stage: number | null; bucket: BucketKey | null };
const NO_FOCUS: Focus = { stage: null, bucket: null };

export function NaiveDashboard({ active }: { active: boolean }) {
  const { data, lastOk, error, failures, conn, now, polling, snapshotAt } = usePipeline(active);
  const [focus, setFocus] = useState<Focus>(NO_FOCUS);
  const [dismissed, setDismissed] = useState(() => {try{return localStorage.getItem('playlattice-dismissed-warnings') ?? '';}catch{return '';}});
  const [controlBusy, setControlBusy] = useState(false);
  const [controlMessage, setControlMessage] = useState<string | null>(null);
  const warningKey = JSON.stringify([error, data?.batch?.id, data?.batch?.status, data?.policy?.enabled, data?.batch?.items.filter(i => i.error).map(i => [i.id,i.error]), data?.workflows.filter(w => w.publication?.error).map(w => [w.job_id,w.publication?.error])]);
  const warningsDismissed = dismissed === warningKey;
  const clearWarnings = () => {setDismissed(warningKey);try{localStorage.setItem('playlattice-dismissed-warnings',warningKey);}catch{}};
  const cancelBatch = async () => {
    if (!data?.batch?.id || !window.confirm('Cancel this batch? Queued work will stop and active work will receive cancellation requests. Completed results and existing posts will be kept.')) return;
    setControlBusy(true);setControlMessage(null);
    try {
      const response = await fetch(`${ENDPOINT}/cancel?batch_id=${encodeURIComponent(data.batch.id)}`, {method:'POST',headers:{'X-PlayLattice-Control':'1'}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error ?? 'Could not cancel batch');
      setControlMessage(result.analysis_cancel_errors?.length ? 'Batch cancelled. Some active analyses could not be stopped; review their job status.' : 'Batch cancelled. Completed results and existing posts were kept.');
    } catch(e) {setControlMessage(e instanceof Error ? e.message : 'Could not cancel batch');}
    finally {setControlBusy(false);}
  };
  const [deletedIds, setDeletedIds] = useState<Set<string>>(() => new Set());
  const [deleteBusy, setDeleteBusy] = useState<string | null>(null);
  const deleteEntry = async (item: BatchItem | 'all') => {
    const cancelledRows = data?.batch?.items.filter((i) => i.status === 'cancelled' && !deletedIds.has(i.id)) ?? [];
    const label = item === 'all' ? `all ${cancelledRows.length} cancelled video entries` : `the entry for “${item.name || item.id}”`;
    if (!data?.batch?.id || !window.confirm(`Delete ${label} from this list? Only cancelled videos can be deleted. Source videos, analyses and any results are kept.`)) return;
    const key = item === 'all' ? 'all' : item.id;
    setDeleteBusy(key); setControlMessage(null);
    try {
      const target = item === 'all' ? 'all_cancelled=1' : `item_id=${encodeURIComponent(item.id)}`;
      const response = await fetch(`${ENDPOINT}/items/delete?batch_id=${encodeURIComponent(data.batch.id)}&${target}`, { method: 'POST', headers: { 'X-PlayLattice-Control': '1' } });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error ?? `Could not delete entry (HTTP ${response.status})`);
      const ids: string[] = Array.isArray(result.deleted) ? result.deleted : [key];
      setDeletedIds((prev) => { const next = new Set(prev); for (const id of ids) next.add(id); return next; });
      setControlMessage(item === 'all' ? `Deleted ${ids.length} cancelled video entr${ids.length === 1 ? 'y' : 'ies'}.` : `Deleted the entry for ${item.name || item.id}.`);
    } catch (e) { setControlMessage(e instanceof Error ? e.message : 'Could not delete entry'); }
    finally { setDeleteBusy(null); }
  };
  // Hide just-deleted rows until the next snapshot confirms the deletion.
  const batchView = useMemo(() => data?.batch && deletedIds.size ? { ...data.batch, items: data.batch.items.filter((i) => !deletedIds.has(i.id)) } : data?.batch ?? null, [data?.batch, deletedIds]);
  const videoName = (jobId: string) => data?.batch?.items.find((i) => i.job_id === jobId)?.name ?? null;
  const posting = data ? postingState(data, now) : null;
  const postingBlocked = data?.batch?.status === 'provider_blocked' || data?.policy?.enabled === false;

  return (
    <div className="naive">
      <section className="panel naive-head">
        <div className="panel-head">
          <div>
            <h2>Naive Crew Pipeline</h2>
            <p className="muted small">How each authorized clip becomes a TikTok portrait quiz, and where the batch is stuck. Manage the batch and review results from the local Naive integration.</p>
          </div>
          <div className="naive-pills">
            <div className="naive-pill" role="status" aria-live="polite" title="Whether this dashboard is receiving snapshots. It does not mean the workflow is healthy.">
              <span className={`naive-dot ${conn}`} aria-hidden="true" />
              <span><span className="naive-pill-k">Data feed</span><b>{CONN_LABEL[conn]}</b></span>
              <span className="muted small">{polling ? 'polling…' : `auto · ${POLL_MS / 1000}s`}</span>
            </div>
            {posting && (
              <div className={`naive-pill posting ${posting.tone}`} title="Whether new TikTok posts can go out, from batch status and publishing policy.">
                <span className="naive-pill-icon" aria-hidden="true">{posting.tone === 'bad' ? '⛔' : posting.tone === 'warn' ? '⏸' : posting.tone === 'ok' ? '▶' : '•'}</span>
                <span><span className="naive-pill-k">Posting</span><b>{posting.pill}</b></span>
              </div>
            )}
          </div>
        </div>
        <div className="naive-controls">
          <button className="secondary small" onClick={clearWarnings} disabled={warningsDismissed}>Clear Warnings</button>
          {warningsDismissed && <button className="ghost small" onClick={() => {setDismissed('');try{localStorage.removeItem('playlattice-dismissed-warnings');}catch{}}}>Show Warnings</button>}
          <button className="ghost small danger" disabled={controlBusy || !data?.batch?.id || data.batch.status === 'cancelled' || !!controlMessage?.startsWith('Batch cancelled')} onClick={() => void cancelBatch()}>{controlBusy ? 'Cancelling…' : data?.batch?.status === 'cancelled' ? 'Batch Cancelled' : 'Cancel Batch'}</button>
          <span className="muted small">Warnings clear on this browser; history is kept.</span>
        </div>
        {controlMessage && <p role="status">{controlMessage}</p>}
        <p className="naive-feednote small muted">
          Snapshot {ago(snapshotAt, now)} · last successful poll {ago(lastOk, now)}
          {data?.batch?.updated_at !== undefined && <> · queue last wrote its state {ago(toMs(data.batch.updated_at), now)}</>}
          . “Live” only means the feed is connected.
        </p>
        {error && !warningsDismissed && (
          <div className={data ? 'alert warn' : 'alert error'}>
            {data
              ? `Pipeline endpoint unreachable (${error}; ${failures} failed poll${failures === 1 ? '' : 's'}). Showing the last snapshot from ${ago(lastOk, now)} — it may be out of date.`
              : `Pipeline endpoint unavailable: ${error}. Retrying every ${POLL_MS / 1000}s. Start the Naive integration on 127.0.0.1:8011 to see live data.`}
          </div>
        )}
        {data && posting && !warningsDismissed && <StatusBanner data={data} posting={posting} focus={focus} setFocus={setFocus} stale={conn !== 'live'} />}
      </section>

      {!data || !posting ? (
        <section className="panel"><p className="muted small">{conn === 'connecting' ? 'Waiting for the first pipeline snapshot…' : 'No pipeline data received yet. Nothing is shown until the endpoint reports it.'}</p></section>
      ) : (
        <div className={conn === 'live' ? 'naive-body' : 'naive-body dim'}>
          <MailroomPanel mail={data.agentmail} outreach={data.services.outreach} now={now} />
          <details className="panel naive-more naive-flow-details">
            <summary><h3>Workflow Stages</h3><span className="muted small">View stage counts and filter videos</span></summary>
            <Flow data={data} focus={focus} setFocus={setFocus} blocked={postingBlocked} />
          </details>
          <div className="naive-grid">
            <NowPanel data={data} blocked={postingBlocked} />
            <PostsPanel data={data} now={now} />
          </div>
          <TeamActivityPanel activity={data.team_activity} videoName={videoName} now={now} feedLive={conn === 'live'} />
          <BatchTable batch={batchView} focus={focus} setFocus={setFocus} onDelete={deleteEntry} deleting={deleteBusy} />
          <details className="panel naive-more">
            <summary><h3>Crew & Budget</h3><span className="muted small">Roles, usage, and spending limits</span></summary>
            <div className="naive-grid secondary"><CrewRoles crew={data.crew} workflows={data.workflows} /><BudgetPanel budget={data.budget} workflows={data.workflows} /></div>
          </details>
          <details className="panel naive-more">
            <summary><h3>Crew workflow history</h3><span className="muted small">all batches · {plural(data.workflows.length, 'job')} · filters, costs, publication errors</span></summary>
            <WorkflowTable workflows={data.workflows} crew={data.crew} now={now} />
          </details>
          <details className="panel naive-more">
            <summary><h3>Technical details</h3><span className="muted small">endpoint, services, policy, batch & agent IDs</span></summary>
            <TechDetails data={data} lastOk={lastOk} snapshotAt={snapshotAt} />
          </details>
        </div>
      )}
    </div>
  );
}

function MailroomPanel({mail, outreach, now}: {mail: MailStatus | null; outreach?: string; now:number}) {
  const requests = mail?.requests ?? {}, replies = mail?.replies ?? {};
  const queued = (requests.queued ?? 0) + (requests.rendering ?? 0);
  return <section className="panel naive-mailroom" aria-labelledby="mailroom-title">
    <div className="panel-head"><div><h3 id="mailroom-title">AgentMail & Outreach</h3><p className="muted small">Inbound email → Naive outreach → source clip → preview reply</p></div><Status value={mail?.worker ?? 'unknown'} /></div>
    <div className="naive-mailroom-summary">
      <span>Outreach <Status value={outreach ?? 'unknown'} /></span>
      <span><b>{queued}</b> Queued / Rendering</span><span><b>{requests.ready ?? 0}</b> Previews Ready</span>
      <span><b>{replies.sent ?? 0}</b> Replies Sent</span><span><b>{requests.failed ?? 0}</b> Failed Clips</span>
      <span><b>{(replies.pending ?? 0) + (replies.sending ?? 0)}</b> Replies Pending</span>
    </div>
    <p className="muted small">Worker checked {ago(toMs(mail?.updated_at),now)} · Last successful inbox poll {ago(toMs(mail?.last_success_at),now)}</p>
    {mail?.worker === 'error' && <p className="alert warn">Inbox polling failed{mail.error_code ? ` (HTTP ${mail.error_code})` : ''}. The worker will retry.</p>}
    {(!mail || ['unknown','stopped','stale'].includes(mail.worker ?? 'unknown')) && <p className="muted small">Worker health is {words(mail?.worker ?? 'unknown')}. Completed outreach sessions do not mean the inbox worker is online.</p>}
    {mail?.ledger !== 'ok' && <p className="muted small">Request and reply counts are unavailable.</p>}
    {(replies.needs_reconciliation ?? 0) > 0 && <p className="alert warn">{replies.needs_reconciliation} replies need delivery reconciliation.</p>}
    {!!mail?.recent?.length && <details><summary>Recent Clip Requests</summary><div className="table-scroll"><table><thead><tr><th>Request</th><th>Status</th><th>Updated</th></tr></thead><tbody>{mail.recent.map(r=><tr key={r.id}><td><code>{r.id.slice(0,12)}</code></td><td><Status value={r.status} /></td><td>{ago(r.updated*1000,now)}</td></tr>)}</tbody></table></div></details>}
  </section>;
}

function StatusBanner({ data, posting, focus, setFocus, stale }: {
  data: Pipeline; posting: ReturnType<typeof postingState>; focus: Focus; setFocus: (f: Focus) => void; stale: boolean;
}) {
  const b = data.batch;
  const prog = b ? batchProgress(b) : null;
  const waiting = b?.items.filter((i) => i.status === 'running') ?? [];
  return (
    <section className={`panel naive-banner ${posting.tone}`} aria-labelledby="naive-banner-h">
      <div className="naive-banner-top">
        <span className="naive-banner-icon" aria-hidden="true">{posting.tone === 'bad' ? '⛔' : posting.tone === 'warn' ? '⏸' : posting.tone === 'ok' ? '▶' : 'ⓘ'}</span>
        <div>
          <p className="naive-kicker">Current status{stale && ' · last known (feed not live)'}</p>
          <h3 id="naive-banner-h">{posting.headline}</h3>
          {posting.body && <p>{posting.body}</p>}
          {posting.notes.map((n) => <p key={n}>{n}</p>)}
          {waiting.filter((w) => w.error).slice(0, 1).map((w) => (
            <p key={w.id} className="naive-banner-quote">Latest note on <b>{w.name || w.id}</b>: “{w.error}”</p>
          ))}

        </div>
      </div>
      {b && prog && (
        <div className="naive-progress">
          <div className="naive-progress-head">
            <h4>Batch progress</h4>
            <span className="muted small">
              {prog.n.posted + prog.n.skipped + prog.n.failed + prog.n.other} of {prog.total} finished · from {prog.source}
              {prog.unaccounted > 0 && ` · ${prog.unaccounted} not in counts`}
            </span>
          </div>
          <div className="naive-bar" role="img" aria-label={BUCKETS.filter((x) => prog.n[x.key] > 0).map((x) => `${prog.n[x.key]} ${x.label.toLowerCase()}`).join(', ') + ` of ${prog.total}`}>
            {prog.total > 0 && BUCKETS.map((x) => prog.n[x.key] > 0 && <i key={x.key} className={x.key} style={{ width: `${(prog.n[x.key] / prog.total) * 100}%` }} />)}
          </div>
          <div className="naive-legend" role="group" aria-label="Filter batch videos by outcome">
            {BUCKETS.filter((x) => x.key !== 'other' || prog.n.other > 0).map((x) => {
              const on = focus.bucket === x.key;
              return (
                <button key={x.key} type="button" className={`naive-legend-btn ${x.key}${on ? ' on' : ''}`} aria-pressed={on} title={x.hint}
                  onClick={() => setFocus({ ...focus, bucket: on ? null : x.key })}>
                  <i aria-hidden="true" /><b>{prog.n[x.key]}</b><span>{x.label}</span>
                </button>
              );
            })}
          </div>
          <p className="muted small naive-legend-note">Counts are this batch only ({b.id ? <code>{b.id}</code> : 'unnamed'}). Click an outcome to list those videos.</p>
        </div>
      )}
    </section>
  );
}

function Flow({ data, focus, setFocus }: { data: Pipeline; focus: Focus; setFocus: (f: Focus) => void; blocked: boolean }) {
  const items = data.batch?.items ?? [];
  const cancelled = data.batch?.status === 'cancelled';
  const liveRuns = data.team_activity?.runs.filter(r => r.current) ?? [];
  const unmapped = items.filter(it => stageIndex(it.stage) === null).length;
  return (
    <section className="panel naive-flow-panel" aria-labelledby="naive-flow-h">
      <div className="panel-head"><div>
        <h3 id="naive-flow-h">Workflow Stages</h3>
        <p className="muted small">Batch: {data.batch?.id ?? 'None'} · {cancelled ? 'Cancelled — counts show where videos last stopped.' : 'Counts show each video’s last recorded stage.'} Click a stage to filter the video list.</p>
      </div>{focus.stage !== null && <button className="ghost small" onClick={() => setFocus({...focus,stage:null})}>Show All Steps</button>}</div>
      <div className="naive-table-wrap"><table className="player-table naive-stage-table">
        <thead><tr><th scope="col">Step</th><th scope="col">Owner / Work</th><th scope="col">Recorded Videos</th><th scope="col">Outcomes</th></tr></thead>
        <tbody>{FLOW.map((step,index) => {
          const here=items.filter(it => stageIndex(it.stage) === index);
          const counts=new Map<string,number>();
          for (const it of here) counts.set(it.status ?? 'unknown',(counts.get(it.status ?? 'unknown') ?? 0)+1);
          return <tr key={step.key} className={focus.stage === index ? 'on' : undefined}>
            <th scope="row"><button className="ghost small" aria-pressed={focus.stage === index} onClick={() => setFocus({...focus,stage:focus.stage === index ? null : index})}>{index+1}. {step.label}</button></th>
            <td><strong>{step.owner}</strong><span className="muted small naive-role">{step.description}</span></td>
            <td>{here.length}</td>
            <td>{counts.size ? [...counts].map(([status,count]) => <span key={status} className={`status ${statusClass(status)}`}>{count} {words(status)}</span>) : <span className="muted">None Recorded</span>}</td>
          </tr>;
        })}</tbody>
      </table></div>
      <p className="small"><b>Live Crew Activity:</b> {liveRuns.length ? `${liveRuns.length} active run${liveRuns.length === 1 ? '' : 's'}${liveRuns.some(r => r.cancellation_requested) ? ' (includes stopping runs)' : ''}. See Crew Activity for tasks and handoffs.` : 'No active runs reported.'} {data.policy?.enabled === false && 'Automatic posting is off.'}</p>
      {unmapped > 0 && <p className="muted small">{unmapped} videos have no recognized stage. They remain in the video list.</p>}
    </section>
  );
}

function StepPips({ stage }: { stage: string | undefined }) {
  const idx = stageIndex(stage);
  return (
    <span className="naive-pips" aria-hidden="true">{FLOW.map((s, i) => <i key={s.key} className={idx === null ? '' : i < idx ? 'past' : i === idx ? 'now' : ''} />)}</span>
  );
}

function NowPanel({ data, blocked }: { data: Pipeline; blocked: boolean }) {
  const items = data.batch?.items ?? [];
  const running = items.filter((i) => i.status === 'running');
  const cur = running[0];
  const next = items.find((i) => i.status === 'queued');
  const idx = cur ? stageIndex(cur.stage) : null;
  return (
    <section className="panel naive-now" aria-labelledby="naive-now-h">
      <div className="panel-head"><h3 id="naive-now-h">Current video</h3>{running.length > 1 && <span className="muted small">+{running.length - 1} more in progress</span>}</div>
      {!data.batch ? <p className="muted small">No batch reported.</p> : !cur ? (
        <>
          <p className="small">No video is reported as in progress.</p>
          {next && <p className="muted small">Next queued: <b>{next.name || next.id}</b> (not started)</p>}
        </>
      ) : (
        <>
          <VideoThumbnail className="naive-now-thumbnail" gameId={cur.game_id} title={cur.name || cur.id} />
          <p className="naive-now-name"><SafeLink href={cur.source_url}>{cur.name || cur.id}</SafeLink> <Status value={data.batch?.status === 'provider_blocked' ? 'waiting' : cur.status} /></p>
          <p className="naive-now-stage">
            <StepPips stage={cur.stage} />
            <span>{idx !== null ? <>Step {idx + 1} of {FLOW.length} · <b>{FLOW[idx].label}</b></> : 'Stage not recognised'} <span className="muted">({words(cur.stage)})</span></span>
          </p>
          {cur.error && <p className={blocked ? 'naive-now-err bad' : 'naive-now-err'}>{cur.error}</p>}
          <p className="naive-now-links small">
            {cur.preview_url && <SafeLink href={cur.preview_url}>Preview ↗</SafeLink>}
            {cur.post_url && <SafeLink href={cur.post_url}>TikTok ↗</SafeLink>}
          </p>
          {cur.camera_evidence && <p className="muted small">Camera: {cur.camera_evidence}</p>}
          {next && <p className="muted small">Next queued: {next.name || next.id} (not started)</p>}
        </>
      )}
    </section>
  );
}

function PostsPanel({ data, now }: { data: Pipeline; now: number }) {
  const fromBatch = (data.batch?.items ?? []).filter((i) => i.post_url)
    .sort((a, b) => (toMs(b.completed_at) ?? 0) - (toMs(a.completed_at) ?? 0));
  const pubs = data.workflows.filter((w) => w.publication);
  const lifetimePosted = pubs.filter((w) => w.publication?.url || w.publication?.status === 'posted').length;
  const fromHistory = fromBatch.length ? [] : pubs.filter((w) => w.publication?.url)
    .sort((a, b) => (toMs(b.completed_at) ?? 0) - (toMs(a.completed_at) ?? 0));
  return (
    <section className="panel naive-posts" aria-labelledby="naive-posts-h">
      <div className="panel-head">
        <h3 id="naive-posts-h">Latest posts</h3>
        <span className="muted small">{fromBatch.length ? `${fromBatch.length} in this batch` : 'none in this batch'}</span>
      </div>
      {fromBatch.length === 0 && fromHistory.length === 0 && <p className="muted small">No posted videos reported.</p>}
      <ul className="naive-postlist">
        {fromBatch.slice(0, 3).map((i) => (
          <li key={i.id}>
            <span className="naive-post-name"><b>{i.name || i.id}</b><span className="muted small">{toMs(i.completed_at) !== null ? `posted ${ago(toMs(i.completed_at), now)}` : 'post time not reported'}</span></span>
            <span className="naive-post-links">
              {i.preview_url && <SafeLink className="naive-linkbtn" href={i.preview_url}>Preview ↗</SafeLink>}
              <SafeLink className="naive-linkbtn primary" href={i.post_url}>TikTok ↗</SafeLink>
            </span>
          </li>
        ))}
        {fromHistory.slice(0, 3).map((w) => (
          <li key={w.job_id}>
            <span className="naive-post-name"><b>Job {short(w.job_id)}</b><span className="muted small">from workflow history</span></span>
            <span className="naive-post-links">
              {w.presentation?.preview_uri && <SafeLink className="naive-linkbtn" href={w.presentation.preview_uri}>Preview ↗</SafeLink>}
              <SafeLink className="naive-linkbtn primary" href={w.publication?.url}>TikTok ↗</SafeLink>
            </span>
          </li>
        ))}
      </ul>
      <p className="muted small">All batches (workflow history): {plural(lifetimePosted, 'publication')} posted of {plural(pubs.length, 'record')}.</p>
    </section>
  );
}

const EMPTY_ITEMS: BatchItem[] = [];
function BatchTable({ batch, focus, setFocus, onDelete, deleting }: { batch: Batch | null; focus: Focus; setFocus: (f: Focus) => void; onDelete: (item: BatchItem | 'all') => void; deleting: string | null }) {
  const all = batch?.items ?? EMPTY_ITEMS;
  const items = useMemo(() => all.filter((i) =>
    (focus.stage === null || stageIndex(i.stage) === focus.stage) && (focus.bucket === null || inBucket(i.status, focus.bucket))), [all, focus]);
  const c = useTableControls(items, (i) => [i.id, i.name, i.stage, i.status, i.game_id, i.job_id, i.error].join(' '),
    { status: (i) => i.status, stage: (i) => i.stage }, `${focus.stage}-${focus.bucket}`);
  const cancelledCount = all.filter((i) => i.status === 'cancelled').length;
  const focused = focus.stage !== null || focus.bucket !== null;
  const focusText = [focus.stage !== null && `Step ${focus.stage + 1} · ${FLOW[focus.stage].label}`, focus.bucket && BUCKETS.find((b) => b.key === focus.bucket)?.label].filter(Boolean).join(' + ');
  return (
    <section className="panel" id="naive-batch-list" aria-labelledby="naive-batch-h">
      <div className="panel-head">
        <h3 id="naive-batch-h">Batch Videos</h3>
        <span className="naive-batch-actions">
          <span className="muted small">{batch ? plural(all.length, 'item') : 'no active batch'}{batch?.deleted_count ? ` · ${batch.deleted_count} cancelled deleted` : ''}</span>
          {cancelledCount > 0 && <button type="button" className="ghost small danger" disabled={deleting !== null} onClick={() => onDelete('all')}>{deleting === 'all' ? 'Deleting…' : `Delete All Cancelled (${cancelledCount})`}</button>}
        </span>
      </div>
      {focused && (
        <div className="naive-focusbar" role="status">
          <span>Showing <b>{focusText}</b> — {items.length} of {all.length} videos</span>
          <button type="button" className="secondary small" onClick={() => setFocus(NO_FOCUS)}>Show All Videos</button>
        </div>
      )}
      {!batch || all.length === 0 ? <p className="muted small">{batch ? 'The batch has no items yet.' : 'No discovery batch is running.'}</p> : (<>
        <TableToolbar id="naive-batch" c={c} placeholder="Name, id, game, job, error…" />
        <div className="player-table-scroll">
          <table className="player-table naive-table">
            <thead><tr><th scope="col">Video</th><th scope="col">Step</th><th scope="col">Status</th><th scope="col">Preview</th><th scope="col">TikTok</th></tr></thead>
            <tbody>
              {c.visible.length === 0 && <tr><td colSpan={5} className="muted">No videos match these filters.</td></tr>}
              {c.visible.map((it) => {
                const idx = stageIndex(it.stage);
                return (
                  <tr key={it.id} className={statusClass(it.status) === 'failed' ? 'naive-row-bad' : statusClass(it.status) === 'cancelled' ? 'naive-row-off' : undefined}>
                    <th scope="row" className="naive-wrap">
                      <div className="naive-video-cell">
                      <VideoThumbnail gameId={it.game_id} title={it.name || it.id} />
                      <div>
                      <SafeLink href={it.source_url}>{it.name || short(it.id)}</SafeLink>
                      {it.error && <span className="naive-err">{it.error}</span>}
                      {(it.game_id || it.job_id || it.camera_evidence) && (
                        <details className="naive-ids">
                          <summary>Details</summary>
                          <span>id <code>{it.id}</code></span>
                          {it.game_id && <span>game <code>{it.game_id}</code></span>}
                          {it.job_id && <span>job <code>{it.job_id}</code></span>}
                          {it.camera_evidence && <span>camera: {it.camera_evidence}</span>}
                        </details>
                      )}
                      </div>
                      </div>
                    </th>
                    <td>
                      <span className="naive-stagecell">
                        <StepPips stage={it.stage} />
                        <span>{idx !== null ? FLOW[idx].label : words(it.stage)}</span>
                        <span className="table-note">{words(it.stage)}</span>
                      </span>
                    </td>
                    <td>
                      <Status value={it.status} />
                      {it.status === 'cancelled' && (
                        <button type="button" className="ghost small naive-delete" disabled={deleting !== null} aria-label={`Delete entry for ${it.name || it.id}`} onClick={() => onDelete(it)}>
                          {deleting === it.id ? 'Deleting…' : 'Delete Entry'}
                        </button>
                      )}
                    </td>
                    <td><SafeLink href={it.preview_url}>Preview ↗</SafeLink></td>
                    <td><SafeLink href={it.post_url}>Post ↗</SafeLink></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <Pager c={c} total={items.length} noun="videos" />
      </>)}
    </section>
  );
}

const ROLE_INFO: Record<string, { what: string; step: string }> = {
  ceo: { what: 'Coordinates the crew on each job', step: 'Analyze Play' },
  scout: { what: 'Reads the local analysis evidence', step: 'Analyze Play' },
  clipper: { what: 'Picks source-bound clips, then quiz moments & music', step: 'Analyze Play · Portrait Quizzes' },
  'caption-editor': { what: 'Writes hedged captions (identities stay unverified)', step: 'Analyze Play' },
  'channel-manager': { what: 'Owns the publication hand-off to the local publisher', step: 'Post To TikTok' },
  analyst: { what: 'Feedback review of the package', step: 'Analyze Play' },
  outreach: { what: 'Inbound AgentMail conversations, catalog recommendations, and clip-request clarification', step: 'Customer Email' },
};

function CrewRoles({ crew, workflows }: { crew: CrewMember[]; workflows: Workflow[] }) {
  const sessions = new Map<string, number>();
  for (const w of workflows) for (const m of w.members ?? []) if (m.agent_id) sessions.set(m.agent_id, (sessions.get(m.agent_id) ?? 0) + 1);
  return (
    <section className="panel naive-crew" aria-labelledby="naive-crew-h">
      <div className="panel-head"><h3 id="naive-crew-h">Who does what</h3><span className="muted small">{plural(crew.length, 'Naive agent')}</span></div>
      {crew.length === 0 ? <p className="muted small">No crew reported.</p> : (
        <ul className="naive-rolelist">
          {crew.map((c, i) => {
            const key = (c.name ?? c.role ?? '').toLowerCase();
            const info = ROLE_INFO[key];
            const n = typeof c.session_count === 'number' ? c.session_count : c.id ? sessions.get(c.id) : undefined;
            return (
              <li key={c.id ?? i}>
                <i className="tag agent" aria-hidden="true">Naive</i>
                <span><b>{c.name ?? '–'}</b><span className="muted small"> · {info ? info.step : 'step not mapped'}</span>
                  <span className="naive-role-what">{info?.what ?? (c.role ? `Role: ${c.role}` : 'Role not reported')}</span></span>
                <span className="muted small naive-role-n" title="Sessions recorded across all workflow history">{n !== undefined ? plural(n, 'session') : key === 'ceo' ? 'coordinator' : 'no sessions listed'}</span>
              </li>
            );
          })}
        </ul>
      )}
      <p className="muted small">Detection, pose, tracking, rendering and the final post request run as local PlayLattice code, not as agents.</p>
    </section>
  );
}

function BudgetPanel({ budget, workflows }: { budget: Budget | null; workflows: Workflow[] }) {
  const cap = budget?.monthly_cap_micro_usd;
  const used = budget?.accounted_micro_usd;
  const pct = typeof cap === 'number' && cap > 0 && typeof used === 'number' ? Math.min(100, (used / cap) * 100) : null;
  const reserved = workflows.filter((w) => statusClass(w.state) === 'running' || statusClass(w.state) === 'queued')
    .reduce((n, w) => n + (typeof w.reserved === 'number' ? w.reserved : 0), 0);
  return (
    <section className="panel naive-spendpanel">
      <div className="panel-head"><h3>Spend</h3><span className="muted small">this month, accounted by the integration</span></div>
      {!budget ? <p className="muted small">No budget reported.</p> : (
        <>
          <p className="naive-spend"><b>{usd(used)}</b> <span className="muted">of {usd(cap)} monthly cap</span></p>
          {pct !== null && (
            <div className={`naive-meter ${pct >= 90 ? 'hot' : pct >= 70 ? 'warm' : ''}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} aria-label="Monthly budget used">
              <div style={{ width: `${pct}%` }} />
            </div>
          )}
          <dl className="naive-meta">
            <div><dt>Per-video cap</dt><dd>{usd(budget.per_video_cap_micro_usd)}</dd></div>
            <div><dt>Reserved by open workflows</dt><dd>{usd(reserved)}</dd></div>
            {pct !== null && <div><dt>Cap used</dt><dd>{pct.toFixed(1)}%</dd></div>}
          </dl>
        </>
      )}
    </section>
  );
}

function TechDetails({ data, lastOk, snapshotAt }: { data: Pipeline; lastOk: number | null; snapshotAt: number | null }) {
  const { services, policy, batch, crew } = data;
  const counts = Object.entries(batch?.counts ?? {}).filter(([, v]) => typeof v === 'number');
  return (
    <div className="naive-tech">
      <dl className="naive-meta">
        <div><dt>Source</dt><dd><code>127.0.0.1:8011/pipeline</code> <span className="muted small">(via {ENDPOINT})</span></dd></div>
        <div><dt>Snapshot updated</dt><dd>{clock(snapshotAt)}</dd></div>
        <div><dt>Last successful poll</dt><dd>{clock(lastOk)}</dd></div>
        {Object.keys(services).length === 0 && <div><dt>Services</dt><dd className="muted">not reported</dd></div>}
        {Object.entries(services).map(([k, v]) => <div key={k}><dt>{words(k)}</dt><dd><Status value={v} /></dd></div>)}
        <div><dt>Auto-publish policy</dt><dd>{policy ? (policy.enabled ? <span className="status succeeded">enabled</span> : <span className="status none">disabled</span>) : <span className="muted">not reported</span>}</dd></div>
        {policy && <div><dt>Platform · visibility</dt><dd>{words(policy.platform)} · {words(policy.visibility)}</dd></div>}
        {policy?.stop_at !== undefined && <div><dt>Posting window ends</dt><dd>{clock(toMs(policy.stop_at))}</dd></div>}
        <div><dt>Batch</dt><dd>{batch ? <><Status value={batch.status} /> <code>{batch.id ?? '–'}</code></> : <span className="muted">none active</span>}</dd></div>
        {batch?.stop_at !== undefined && <div><dt>Batch stops</dt><dd>{clock(toMs(batch.stop_at))}</dd></div>}
        {batch?.updated_at !== undefined && <div><dt>Queue state written</dt><dd>{clock(toMs(batch.updated_at))}</dd></div>}
      </dl>
      {counts.length > 0 && <p className="naive-counts small">Raw batch counts: {counts.map(([k, v]) => <span key={k}><b>{v}</b> {words(k)}</span>)}</p>}
      {crew.length > 0 && (
        <div className="player-table-scroll">
          <table className="player-table naive-table">
            <caption className="muted small">Naive crew agent IDs</caption>
            <thead><tr><th scope="col">Agent</th><th scope="col">Role</th><th scope="col">ID</th></tr></thead>
            <tbody>
              {crew.map((c, i) => (
                <tr key={c.id ?? i}><th scope="row">{c.name ?? '–'}</th><td>{c.role ?? '–'}</td><td><code>{c.id ?? '–'}</code></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function WorkflowTable({ workflows, crew, now }: { workflows: Workflow[]; crew: CrewMember[]; now: number }) {
  const rows = useMemo(() => [...workflows].sort((a, b) => (b.created_ms ?? 0) - (a.created_ms ?? 0)), [workflows]);
  const names = useMemo(() => new Map(crew.filter((c) => c.id).map((c) => [c.id as string, c.name ?? c.role ?? c.id as string])), [crew]);
  const c = useTableControls(rows, (w) => [w.job_id, w.game_id, w.state, w.publication?.status, w.publication?.error, w.publication?.post_id].join(' '),
    { status: (w) => w.state, stage: (w) => (w.publication ? `post ${w.publication.status ?? 'unknown'}` : w.presentation ? 'portrait ready' : 'no portrait') });
  return (
    <div className="naive-more-body">
      <p className="muted small">One row per analysis job across every batch (not just tonight). Analysis → portrait quiz → publication.</p>
      {rows.length === 0 ? <p className="muted small">No workflows reported.</p> : (<>
        <TableToolbar id="naive-wf" c={c} placeholder="Job, game, post id, error…" statusLabel="Analysis" stageLabel="Output" />
        <div className="player-table-scroll">
          <table className="player-table naive-table">
            <thead><tr>
              <th scope="col">Job · Game</th><th scope="col">Analysis</th><th scope="col">Started</th><th scope="col">Finished</th>
              <th scope="col">Cost · Reserved</th><th scope="col">Portrait quiz</th><th scope="col">Publication</th><th scope="col">Crew sessions</th>
            </tr></thead>
            <tbody>
              {c.visible.length === 0 && <tr><td colSpan={8} className="muted">No workflows match these filters.</td></tr>}
              {c.visible.map((w) => {
                const p = w.presentation; const pub = w.publication;
                const created = toMs(w.created_ms); const done = toMs(w.completed_at);
                const music = p?.quiz_music_missing;
                const members = w.members ?? [];
                return (
                  <tr key={w.job_id} className={statusClass(w.state) === 'failed' || statusClass(pub?.status) === 'failed' ? 'naive-row-bad' : statusClass(w.state) === 'cancelled' ? 'naive-row-off' : undefined}>
                    <th scope="row"><code title={w.job_id}>{short(w.job_id)}</code><span className="table-note">game <code title={w.game_id}>{short(w.game_id)}</code></span></th>
                    <td><Status value={w.state} /></td>
                    <td title={clock(created)}>{ago(created, now)}</td>
                    <td title={clock(done)}>{done === null ? '–' : ago(done, now)}</td>
                    <td>{usd(w.cost_micro_usd)}<span className="table-note">reserved {usd(w.reserved)}</span></td>
                    <td className="naive-wrap">
                      {!p ? <span className="muted">–</span> : (
                        <>
                          <SafeLink href={p.preview_uri}>Preview ↗</SafeLink>
                          <span className="table-note">
                            {typeof p.quiz_count === 'number' ? `${p.quiz_count} quiz${p.quiz_count === 1 ? '' : 'zes'}` : 'quizzes –'} · {typeof p.track_count === 'number' ? `${p.track_count} track${p.track_count === 1 ? '' : 's'}` : 'tracks –'}
                          </span>
                          {music !== undefined && music !== false && music !== 0 && <span className="naive-err">music missing{typeof music === 'number' ? ` on ${music}` : ''}</span>}
                        </>
                      )}
                    </td>
                    <td className="naive-wrap">
                      {!pub ? <span className="muted">–</span> : (
                        <>
                          <Status value={pub.status} /> {pub.url && <SafeLink href={pub.url}>TikTok ↗</SafeLink>}
                          <span className="table-note">{[pub.requested_visibility, pub.profile && `@${pub.profile.replace(/^@/, '')}`, pub.post_id && `id ${short(pub.post_id, 10)}`].filter(Boolean).join(' · ') || '–'}</span>
                          {pub.error && <span className="naive-err">{pub.error}</span>}
                        </>
                      )}
                    </td>
                    <td className="naive-wrap">
                      {members.length === 0 ? <span className="muted">–</span> : (
                        <details className="naive-ids">
                          <summary>{plural(members.length, 'agent')}</summary>
                          {members.map((m, k) => (
                            <span key={m.session_id ?? k}>{m.agent_id ? names.get(m.agent_id) ?? short(m.agent_id, 12) : '–'} · {words(m.status)}{m.cost_micro_usd !== undefined && ` · ${usd(m.cost_micro_usd)}`} <code>{m.session_id ?? '–'}</code></span>
                          ))}
                        </details>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <Pager c={c} total={rows.length} noun="workflows" />
      </>)}
    </div>
  );
}
