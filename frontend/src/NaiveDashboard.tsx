import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import './NaiveDashboard.css';

/* Read-only view of the Naive crew pipeline served by the local integration on 127.0.0.1:8011
   (proxied as /naive-api). It never posts, starts or cancels anything; it only renders what the
   endpoint reports. Missing fields are shown as "–", never filled in. */

const ENDPOINT = '/naive-api/pipeline';
const PAGE_SIZE = 15;
const POLL_MS = 4000;
const TIMEOUT_MS = 3500;
/** Snapshot older than this (by its own updated_at) is flagged stale even when the request succeeds. */
const STALE_SNAPSHOT_MS = 30_000;

type BatchItem = {
  id: string; name?: string; source_url?: string; status?: string; stage?: string;
  game_id?: string; job_id?: string; error?: string; preview_url?: string; post_url?: string;
};
type Batch = { id?: string; status?: string; stop_at?: string | number; total?: number; counts?: Record<string, number>; items: BatchItem[] };
type Presentation = { preview_uri?: string; track_count?: number; quiz_count?: number; quiz_music_missing?: boolean | number };
type Publication = { status?: string; url?: string; post_id?: string; error?: string; requested_visibility?: string; profile?: string };
type Workflow = {
  job_id: string; game_id?: string; state?: string; created_ms?: number; completed_at?: string | number;
  reserved?: number; cost_micro_usd?: number; output?: unknown; presentation?: Presentation; publication?: Publication;
};
type Budget = { monthly_cap_micro_usd?: number; per_video_cap_micro_usd?: number; accounted_micro_usd?: number };
type CrewMember = { name?: string; role?: string; id?: string };
type Policy = { enabled?: boolean; platform?: string; visibility?: string };
type Pipeline = {
  updated_at?: string | number; batch: Batch | null; workflows: Workflow[]; budget: Budget | null;
  services: Record<string, string>; crew: CrewMember[]; policy: Policy | null;
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
    workflows: arr<Workflow>(r.workflows, (x) => typeof x.job_id === 'string'),
    budget: (obj(r.budget) as Budget | null) ?? null,
    services,
    crew: arr<CrewMember>(r.crew, () => true),
    policy: (obj(r.policy) as Policy | null) ?? null,
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
    if (!active) return;
    let cancelled = false;
    let ctrl: AbortController | null = null;
    const tick = async () => {
      if (cancelled) return;
      if (document.hidden) { timer.current = window.setTimeout(tick, POLL_MS); return; }
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
        if (!cancelled) { setPolling(false); timer.current = window.setTimeout(tick, POLL_MS); }
      }
    };
    void tick();
    const onVisible = () => { if (!document.hidden) { window.clearTimeout(timer.current); void tick(); } };
    document.addEventListener('visibilitychange', onVisible);
    return () => { cancelled = true; ctrl?.abort(); window.clearTimeout(timer.current); document.removeEventListener('visibilitychange', onVisible); };
  }, [active]);

  useEffect(() => {
    if (!active) return;
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
function SafeLink({ href, children }: { href: string | undefined; children: ReactNode }) {
  if (!href) return <span className="muted">–</span>;
  const ok = /^https?:\/\//i.test(href) || (href.startsWith('/') && !href.startsWith('//'));
  return ok ? <a href={href} target="_blank" rel="noreferrer noopener">{children}</a> : <code title="Not a browser URL">{href}</code>;
}

// ---------- pipeline stages ----------
const STAGES = [
  { key: 'discovery', label: 'Discovery', hint: 'Source videos found' },
  { key: 'raw', label: 'Raw clips', hint: 'Imported into the film room' },
  { key: 'analysis', label: 'Analysis', hint: 'Crew workflows' },
  { key: 'quiz', label: 'Portrait quizzes', hint: 'Rendered presentations' },
  { key: 'post', label: 'TikTok posts', hint: 'Publication records' },
] as const;

/** Best-effort position of a reported stage string on the five-step strip; null if unrecognised. */
function stageIndex(stage: string | undefined): number | null {
  const s = (stage ?? '').toLowerCase();
  if (!s) return null;
  if (/(post|publish|tiktok|upload)/.test(s)) return 4;
  if (/(quiz|portrait|present|render|final_clip|final clip)/.test(s)) return 3;
  if (/(analy|track|workflow|crew|curat)/.test(s)) return 2;
  if (/(raw|clip|download|import|ingest|fetch)/.test(s)) return 1;
  if (/(discover|search|found|queue|candidate)/.test(s)) return 0;
  return null;
}

/** "52 succeeded · 18 empty · 2 cancelled" — every reported state, most common first. */
const byState = (vals: (string | undefined)[]) => {
  const m = new Map<string, number>();
  for (const v of vals) m.set(v ?? 'unknown', (m.get(v ?? 'unknown') ?? 0) + 1);
  return [...m].sort((x, y) => y[1] - x[1]).map(([k, n]) => `${n} ${words(k)}`).join(' · ');
};

function stageTotals(p: Pipeline) {
  const items = p.batch?.items ?? [];
  const wf = p.workflows;
  const pubs = wf.filter((w) => w.publication);
  const published = pubs.filter((w) => w.publication?.url || statusClass(w.publication?.status) === 'succeeded').length;
  const pubFailed = pubs.filter((w) => statusClass(w.publication?.status) === 'failed').length;
  const pres = wf.filter((w) => w.presentation);
  const quizzes = pres.reduce((n, w) => n + (typeof w.presentation?.quiz_count === 'number' ? w.presentation.quiz_count : 0), 0);
  const cls = (s?: string) => statusClass(s);
  return [
    { main: p.batch ? (typeof p.batch.total === 'number' ? p.batch.total : items.length) : null, sub: p.batch ? `${items.length} listed` : 'no active batch' },
    { main: p.batch ? items.filter((i) => i.game_id).length : null, sub: p.batch ? `of ${items.length} listed have a game` : '–' },
    { main: wf.length, sub: byState(wf.map((w) => w.state)) || 'none reported' },
    { main: pres.length, sub: `${quizzes} quiz${quizzes === 1 ? '' : 'zes'} across presentations` },
    { main: published, sub: `${pubs.length} record${pubs.length === 1 ? '' : 's'} · ${pubFailed} failed` },
  ];
}

// ---------- table controls ----------
const uniq = (vals: (string | undefined)[]) => [...new Set(vals.filter((v): v is string => !!v))].sort();

/** Search + up to two select filters + fixed-size pages. Page survives polling; resets when filters change. */
function useTableControls<T>(rows: T[], text: (r: T) => string, fields: { status: (r: T) => string | undefined; stage?: (r: T) => string | undefined }) {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [stage, setStage] = useState('');
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [query, status, stage]);
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
      {c.active && <button type="button" className="ghost small" onClick={c.reset}>Clear filters</button>}
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

export function NaiveDashboard({ active }: { active: boolean }) {
  const { data, lastOk, error, failures, conn, now, polling, snapshotAt } = usePipeline(active);

  return (
    <div className="naive">
      <section className="panel naive-head">
        <div className="panel-head">
          <div>
            <h2>Naive Crew Pipeline</h2>
            <p className="muted small">Discovery → raw clips → analysis → portrait quizzes → TikTok posts. Read-only; values come straight from the local Naive integration.</p>
          </div>
          <div className="naive-conn" role="status" aria-live="polite">
            <span className={`naive-dot ${conn}`} aria-hidden="true" />
            <b>{CONN_LABEL[conn]}</b>
            <span className="muted small">{polling ? 'polling…' : `every ${POLL_MS / 1000}s`}</span>
          </div>
        </div>
        <dl className="naive-meta">
          <div><dt>Snapshot updated</dt><dd title={clock(snapshotAt)}>{ago(snapshotAt, now)}</dd></div>
          <div><dt>Last successful poll</dt><dd title={clock(lastOk)}>{ago(lastOk, now)}</dd></div>
          <div><dt>Source</dt><dd><code>127.0.0.1:8011/pipeline</code></dd></div>
        </dl>
        {error && (
          <div className={data ? 'alert warn' : 'alert error'}>
            {data
              ? `Pipeline endpoint unreachable (${error}; ${failures} failed poll${failures === 1 ? '' : 's'}). Showing the last snapshot from ${ago(lastOk, now)} — it may be out of date.`
              : `Pipeline endpoint unavailable: ${error}. Retrying every ${POLL_MS / 1000}s. Start the Naive integration on 127.0.0.1:8011 to see live data.`}
          </div>
        )}
      </section>

      {!data ? (
        <section className="panel"><p className="muted small">{conn === 'connecting' ? 'Waiting for the first pipeline snapshot…' : 'No pipeline data received yet. Nothing is shown until the endpoint reports it.'}</p></section>
      ) : (
        <div className={conn === 'live' ? 'naive-body' : 'naive-body dim'}>
          <StageStrip data={data} />
          <div className="naive-grid">
            <BudgetPanel budget={data.budget} workflows={data.workflows} />
            <OpsPanel services={data.services} policy={data.policy} batch={data.batch} />
          </div>
          <BatchTable batch={data.batch} />
          <WorkflowTable workflows={data.workflows} now={now} />
          <CrewTable crew={data.crew} />
        </div>
      )}
    </div>
  );
}

function StageStrip({ data }: { data: Pipeline }) {
  const totals = stageTotals(data);
  return (
    <ol className="naive-stages" aria-label="Pipeline stages">
      {STAGES.map((s, i) => (
        <li key={s.key}>
          <span className="naive-stage-n">{i + 1}</span>
          <div>
            <h4>{s.label}</h4>
            <b className="naive-stage-v">{totals[i].main ?? '–'}</b>
            <span className="naive-stage-sub">{totals[i].sub}</span>
            <span className="naive-stage-hint">{s.hint}</span>
          </div>
        </li>
      ))}
    </ol>
  );
}

function BudgetPanel({ budget, workflows }: { budget: Budget | null; workflows: Workflow[] }) {
  const cap = budget?.monthly_cap_micro_usd;
  const used = budget?.accounted_micro_usd;
  const pct = typeof cap === 'number' && cap > 0 && typeof used === 'number' ? Math.min(100, (used / cap) * 100) : null;
  const reserved = workflows.filter((w) => statusClass(w.state) === 'running' || statusClass(w.state) === 'queued')
    .reduce((n, w) => n + (typeof w.reserved === 'number' ? w.reserved : 0), 0);
  return (
    <section className="panel">
      <div className="panel-head"><h3>Spend</h3><span className="muted small">accounted by the integration</span></div>
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

function OpsPanel({ services, policy, batch }: { services: Record<string, string>; policy: Policy | null; batch: Batch | null }) {
  const counts = Object.entries(batch?.counts ?? {}).filter(([, v]) => typeof v === 'number');
  return (
    <section className="panel">
      <div className="panel-head"><h3>Services & Policy</h3></div>
      <dl className="naive-meta">
        {Object.keys(services).length === 0 && <div><dt>Services</dt><dd className="muted">not reported</dd></div>}
        {Object.entries(services).map(([k, v]) => <div key={k}><dt>{words(k)}</dt><dd><Status value={v} /></dd></div>)}
        <div><dt>Auto-publish</dt><dd>{policy ? (policy.enabled ? <span className="status succeeded">enabled</span> : <span className="status none">disabled</span>) : <span className="muted">not reported</span>}</dd></div>
        {policy && <div><dt>Platform · visibility</dt><dd>{words(policy.platform)} · {words(policy.visibility)}</dd></div>}
        <div><dt>Batch</dt><dd>{batch ? <><Status value={batch.status} /> <code>{short(batch.id)}</code></> : <span className="muted">none active</span>}</dd></div>
        {batch?.stop_at !== undefined && <div><dt>Batch stops</dt><dd>{clock(toMs(batch.stop_at))}</dd></div>}
      </dl>
      {counts.length > 0 && (
        <p className="naive-counts small">{counts.map(([k, v]) => <span key={k}><b>{v}</b> {words(k)}</span>)}</p>
      )}
    </section>
  );
}

function StageCell({ stage }: { stage: string | undefined }) {
  const idx = stageIndex(stage);
  return (
    <span className="naive-stagecell">
      <span className="naive-pips" aria-hidden="true">{STAGES.map((s, i) => <i key={s.key} className={idx === null ? '' : i < idx ? 'past' : i === idx ? 'now' : ''} />)}</span>
      {words(stage)}
    </span>
  );
}

const EMPTY_ITEMS: BatchItem[] = [];
function BatchTable({ batch }: { batch: Batch | null }) {
  const items = batch?.items ?? EMPTY_ITEMS;
  const c = useTableControls(items, (i) => [i.id, i.name, i.stage, i.status, i.game_id, i.job_id, i.error].join(' '), { status: (i) => i.status, stage: (i) => i.stage });
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>Batch Videos</h3>
        <span className="muted small">{batch ? `${batch.items.length} item${batch.items.length === 1 ? '' : 's'}` : 'no active batch'}</span>
      </div>
      {!batch || batch.items.length === 0 ? <p className="muted small">{batch ? 'The batch has no items yet.' : 'No discovery batch is running.'}</p> : (<>
        <TableToolbar id="naive-batch" c={c} placeholder="Name, id, game, job, error…" />
        <div className="player-table-scroll">
          <table className="player-table naive-table">
            <thead><tr><th scope="col">Video</th><th scope="col">Stage</th><th scope="col">Status</th><th scope="col">Game · Job</th><th scope="col">Preview</th><th scope="col">TikTok</th></tr></thead>
            <tbody>
              {c.visible.length === 0 && <tr><td colSpan={6} className="muted">No videos match these filters.</td></tr>}
              {c.visible.map((it) => (
                <tr key={it.id} className={statusClass(it.status) === 'failed' ? 'naive-row-bad' : statusClass(it.status) === 'cancelled' ? 'naive-row-off' : undefined}>
                  <th scope="row" className="naive-wrap">
                    <SafeLink href={it.source_url}>{it.name || short(it.id)}</SafeLink>
                    {it.error && <span className="naive-err">{it.error}</span>}
                  </th>
                  <td><StageCell stage={it.stage} /></td>
                  <td><Status value={it.status} /></td>
                  <td><code>{short(it.game_id)}</code> · <code>{short(it.job_id)}</code></td>
                  <td><SafeLink href={it.preview_url}>Preview ↗</SafeLink></td>
                  <td><SafeLink href={it.post_url}>Post ↗</SafeLink></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pager c={c} total={items.length} noun="videos" />
      </>)}
    </section>
  );
}

function WorkflowTable({ workflows, now }: { workflows: Workflow[]; now: number }) {
  const rows = useMemo(() => [...workflows].sort((a, b) => (b.created_ms ?? 0) - (a.created_ms ?? 0)), [workflows]);
  const c = useTableControls(rows, (w) => [w.job_id, w.game_id, w.state, w.publication?.status, w.publication?.error, w.publication?.post_id].join(' '),
    { status: (w) => w.state, stage: (w) => (w.publication ? `post ${w.publication.status ?? 'unknown'}` : w.presentation ? 'portrait ready' : 'no portrait') });
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>Crew Workflows</h3>
        <span className="muted small">analysis → portrait quiz → publication, per job</span>
      </div>
      {rows.length === 0 ? <p className="muted small">No workflows reported.</p> : (<>
        <TableToolbar id="naive-wf" c={c} placeholder="Job, game, post id, error…" statusLabel="Analysis" stageLabel="Output" />
        <div className="player-table-scroll">
          <table className="player-table naive-table">
            <thead><tr>
              <th scope="col">Job · Game</th><th scope="col">Analysis</th><th scope="col">Started</th><th scope="col">Finished</th>
              <th scope="col">Cost · Reserved</th><th scope="col">Portrait quiz</th><th scope="col">Publication</th>
            </tr></thead>
            <tbody>
              {c.visible.length === 0 && <tr><td colSpan={7} className="muted">No workflows match these filters.</td></tr>}
              {c.visible.map((w) => {
                const p = w.presentation; const pub = w.publication;
                const created = toMs(w.created_ms); const done = toMs(w.completed_at);
                const music = p?.quiz_music_missing;
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
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <Pager c={c} total={rows.length} noun="workflows" />
      </>)}
    </section>
  );
}

function CrewTable({ crew }: { crew: CrewMember[] }) {
  return (
    <section className="panel">
      <div className="panel-head"><h3>Crew Agents</h3><span className="muted small">{crew.length} agent{crew.length === 1 ? '' : 's'}</span></div>
      {crew.length === 0 ? <p className="muted small">No crew reported.</p> : (
        <div className="player-table-scroll">
          <table className="player-table naive-table">
            <thead><tr><th scope="col">Agent</th><th scope="col">Role</th><th scope="col">ID</th></tr></thead>
            <tbody>
              {crew.map((c, i) => (
                <tr key={c.id ?? i}>
                  <th scope="row">{c.name ?? '–'}</th>
                  <td className="naive-wrap naive-role">{c.role ?? '–'}</td>
                  <td><code title={c.id}>{c.id ? short(c.id, 14) : '–'}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
