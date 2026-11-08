import { Fragment, useState } from 'react';

/* Per-agent crew activity from /pipeline `team_activity`. Every field comes from Naive session
   records (status, stop reason, events). Nothing is inferred beyond what those records show:
   missing values render as "unknown"/"–", and runs that already finished are labelled as such. */

type Entry = { id: string; at: string; kind: string; summary: string; detail?: string; truncated?: boolean; from?: string; to?: string };
type Agent = {
  name: string; agent_id: string; session_id: string | null; status: string | null; stop_reason: string | null; model?: string | null;
  started_at: string | null; ended_at: string | null; last_event_at: string | null;
  state: string; state_label: string; waiting_on: string[]; task: string | null; latest_action: Entry | null;
  tool_calls: number; tool_errors: number; open_tool: string | null; output: string | null; errors: Entry[];
  cost_micro_usd: number | null; pending_actions: string[]; history_truncated: boolean; catching_up: boolean; timeline: Entry[];
};
type Run = { job_id: string; game_id: string | null; workflow_state: string; current: boolean; cancellation_requested?: boolean; root_session_id: string | null; created_ms: number | null; completed_at: string | null; agents: Agent[] };
export type TeamActivity = { fetched_at: string | null; error: string | null; runs: Run[]; limits?: Record<string, number> };

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
/** Keeps only well-formed runs/agents; anything else is dropped rather than guessed. */
export function normalizeActivity(v: unknown): TeamActivity | null {
  if (!isObj(v) || !Array.isArray(v.runs)) return null;
  const runs = v.runs.filter((r): r is Run => isObj(r) && typeof r.job_id === 'string' && Array.isArray(r.agents))
    .map((r) => ({ ...r, agents: r.agents.filter((a): a is Agent => isObj(a) && typeof a.name === 'string')
      .map((a) => ({ ...a, timeline: Array.isArray(a.timeline) ? a.timeline : [], errors: Array.isArray(a.errors) ? a.errors : [], waiting_on: Array.isArray(a.waiting_on) ? a.waiting_on : [], pending_actions: Array.isArray(a.pending_actions) ? a.pending_actions : [] })) }));
  return { fetched_at: typeof v.fetched_at === 'string' ? v.fetched_at : null, error: typeof v.error === 'string' ? v.error : null, runs, limits: isObj(v.limits) ? (v.limits as Record<string, number>) : undefined };
}

const CREW_ORDER = ['CEO', 'scout', 'clipper', 'caption-editor', 'analyst', 'channel-manager'];
const DISPLAY_NAME: Record<string, string> = { CEO: 'Coordinator', scout: 'Scout', clipper: 'Clipper', 'caption-editor': 'Caption Editor', analyst: 'Analyst', 'channel-manager': 'Channel Manager' };

const ROLE: Record<string, string> = {
  CEO: 'Coordinator', scout: 'Source check', clipper: 'Clip selection', 'caption-editor': 'Captions',
  'channel-manager': 'Publication hand-off', analyst: 'Package review', outreach: 'Customer Email',
};
const STATE_TONE: Record<string, string> = {
  working: 'running', waiting_agent: 'wait', waiting_provider: 'wait', waiting_input: 'wait', queued: 'queued', paused: 'wait',
  done: 'succeeded', idle: 'none', stopped: 'failed', failed: 'failed', cancelled: 'cancelled', no_session: 'none', unknown: 'none',
};
const KIND_LABEL: Record<string, string> = { handoff: 'Hand-off', report: 'Report', tool: 'Tool', tool_result: 'Result', message: 'Message', state: 'State', model: 'Model', error: 'Error' };

const ms = (v: string | number | null | undefined) => (typeof v === 'number' ? v : typeof v === 'string' ? Date.parse(v) : NaN);
function ago(v: string | number | null | undefined, now: number) {
  const t = ms(v); if (!Number.isFinite(t)) return '–';
  const s = Math.max(0, Math.round((now - t) / 1000));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`;
}
const time = (v: string | null | undefined) => { const t = ms(v); return Number.isFinite(t) ? new Date(t).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '–'; };
const full = (v: string | number | null | undefined) => { const t = ms(v); return Number.isFinite(t) ? new Date(t).toLocaleString() : ''; };
const usd = (m: number | null) => (typeof m === 'number' && Number.isFinite(m) ? `$${(m / 1e6).toFixed(4)}` : '–');
const ACTIVITY_STALE_MS = 30_000;

export function TeamActivityPanel({ activity, videoName, now, feedLive }: {
  activity: TeamActivity | null; videoName: (jobId: string) => string | null; now: number; feedLive: boolean;
}) {
  const [pick, setPick] = useState<string | null>(null);
  const runs = activity?.runs ?? [];
  const run = runs.find((r) => r.job_id === pick) ?? runs[0] ?? null;
  const fetchedAge = activity?.fetched_at ? now - ms(activity.fetched_at) : null;
  const stale = !feedLive || !!activity?.error || (fetchedAge !== null && fetchedAge > ACTIVITY_STALE_MS);

  return (
    <section className="panel naive-team" aria-labelledby="naive-team-h">
      <div className="panel-head">
        <div>
          <h3 id="naive-team-h">Crew Activity</h3>
          <p className="muted small">Live session status, actions, and handoffs. Expand an agent for details.</p>
        </div>
        <span className={`naive-team-fresh ${stale ? 'stale' : ''}`} title={full(activity?.fetched_at)}>
          {activity === null ? 'activity feed unavailable' : activity.fetched_at ? `Naive sessions checked ${ago(activity.fetched_at, now)}` : 'not fetched yet'}
        </span>
      </div>
      {activity === null ? (
        <p className="muted small">The pipeline endpoint did not include crew activity. The integration on 8011 may be running an older version; restart it to load the activity feed.</p>
      ) : (<>
        {activity.error && <div className="alert warn">Could not refresh Naive sessions ({activity.error}). Showing the last activity fetched {ago(activity.fetched_at, now)}; it may be out of date.</div>}
        {!activity.error && stale && activity.fetched_at && <div className="alert warn">Activity is stale — last fetched {ago(activity.fetched_at, now)}.</div>}
        {runs.length === 0 ? (
          <p className="muted small">{activity.fetched_at ? 'No crew run has a Naive session yet.' : 'Loading crew activity…'}</p>
        ) : (<>
          {runs.length > 1 && (
            <div className="naive-team-runs" role="group" aria-label="Crew run">
              {runs.map((r) => (
                <button key={r.job_id} type="button" className={r === run ? 'secondary small on' : 'ghost small'} aria-pressed={r === run} onClick={() => setPick(r.job_id)}>
                  {videoName(r.job_id) ?? `job ${r.job_id.slice(0, 8)}`} · {r.cancellation_requested ? 'stopping' : r.current ? 'active' : 'history'}
                </button>
              ))}
            </div>
          )}
          {run && <RunView run={run} video={videoName(run.job_id)} now={now} dim={stale} />}
        </>)}
      </>)}
    </section>
  );
}

function RunView({ run, video, now, dim }: { run: Run; video: string | null; now: number; dim: boolean }) {
  const agents = [...run.agents].sort((a, b) => {const rank = (name: string) => {const i = CREW_ORDER.indexOf(name);return i < 0 ? CREW_ORDER.length : i;};return rank(a.name) - rank(b.name);});
  return (<>
    <div className={run.current ? 'naive-team-scope' : 'naive-team-scope past'}>
      <b>{video ?? 'Video not in the current batch'}</b>
      <span className="muted small"> · job <code title={run.job_id}>{run.job_id.slice(0, 8)}</code> · workflow {run.workflow_state.replace(/_/g, ' ')}</span>
      {run.cancellation_requested && <p className="small">Cancellation requested. This run is still reporting activity while it stops.</p>}
      {!run.current && <p className="small">Historical run{run.completed_at ? ` · finished ${ago(run.completed_at, now)}` : ''}. These are recorded states, not current work.</p>}
      {!run.root_session_id && <p className="small muted">This workflow has no Naive session yet, so there is no agent activity to show.</p>}
    </div>
    <div className={dim ? 'naive-team-table-wrap dim' : 'naive-team-table-wrap'} tabIndex={0} role="region" aria-label="Crew Agent Activity">
      <table className="player-table naive-team-table">
        <caption className="naive-team-caption">{run.current ? 'Current Run' : 'Last Finished Run'} · Read top to bottom: coordination, source check, clipping, captions, review, publication.</caption>
        <thead><tr><th scope="col">Agent</th><th scope="col">Status</th><th scope="col">Assigned Work</th><th scope="col">Latest Activity</th><th scope="col">Details</th></tr></thead>
        <tbody>{agents.map((a) => <AgentRow key={`${run.job_id}:${a.agent_id}`} a={a} now={now} current={run.current} />)}</tbody>
      </table>
    </div>
  </>);
}

function AgentRow({ a, now, current }: { a: Agent; now: number; current: boolean }) {
  const [open, setOpen] = useState(false);
  const tone = STATE_TONE[a.state] ?? 'none';
  const lastUpdate = a.last_event_at ?? a.started_at;
  return (
    <Fragment>
      <tr className={`naive-agent-row tone-${tone}`}>
        <th scope="row"><strong>{DISPLAY_NAME[a.name] ?? a.name}</strong><span className="muted small naive-role">{ROLE[a.name] ?? 'Role Unknown'}</span></th>
        <td><span className={`status ${tone}`} title={a.status ? `Session ${a.status}${a.stop_reason ? ` / ${a.stop_reason}` : ''}` : 'No Session'}>{a.state_label}</span>
          {a.waiting_on.length > 0 && <span className="muted small naive-role">Waiting on {a.waiting_on.join(', ')}</span>}
          {a.errors.length > 0 && <span className="naive-err-inline naive-role">{a.errors.length} Recent Error{a.errors.length === 1 ? '' : 's'}</span>}
          {a.pending_actions.length > 0 && <span className="muted small naive-role">Approval Pending</span>}
        </td>
        <td><span className="naive-clamp" title={a.task ?? undefined}>{a.task ?? 'No task recorded'}</span></td>
        <td><span className="naive-clamp" title={a.latest_action?.summary}>{a.latest_action?.summary ?? 'No activity recorded'}</span>
          <span className="naive-agent-updated naive-role" title={full(lastUpdate)}>Updated {ago(lastUpdate, now)}</span>
        </td>
        <td><button type="button" className="ghost small" aria-expanded={open} aria-controls={`agent-details-${a.agent_id}`} onClick={() => setOpen(!open)}>{open ? 'Hide Details' : `View (${a.timeline.length})`}</button></td>
      </tr>
      {open && <tr id={`agent-details-${a.agent_id}`} className="naive-agent-detail-row"><td colSpan={5}>
      <div className="naive-agent-detail">
      <h4>{DISPLAY_NAME[a.name] ?? a.name} · Session Details</h4>
      <dl>
        <div><dt>Task</dt><dd className="naive-clamp" title={a.task ?? undefined}>{a.task ?? <span className="muted">no task message recorded</span>}</dd></div>
        <div><dt>Session</dt><dd>{a.status ? <>{a.status}{a.stop_reason ? ` · ${a.stop_reason.replace(/_/g, ' ')}` : ''}</> : <span className="muted">none</span>}</dd></div>
        <div><dt>Latest action</dt><dd>{a.latest_action ? <><span>{a.latest_action.summary}</span> <span className="muted small" title={full(a.latest_action.at)}>{ago(a.latest_action.at, now)}</span></> : <span className="muted">unknown</span>}</dd></div>
        {open && <div><dt>Started · last update</dt><dd><span title={full(a.started_at)}>{time(a.started_at)}</span> · <span title={full(lastUpdate)}>{ago(lastUpdate, now)}</span></dd></div>}
        <div><dt>Tools</dt><dd>{a.tool_calls} call{a.tool_calls === 1 ? '' : 's'}{a.tool_errors ? <span className="naive-err-inline"> · {a.tool_errors} error{a.tool_errors === 1 ? '' : 's'}</span> : ''}{a.open_tool ? <> · running <code>{a.open_tool}</code></> : ''}</dd></div>
        {open && <div><dt>Output</dt><dd className="naive-clamp" title={a.output ?? undefined}>{a.output ?? <span className="muted">none yet</span>}</dd></div>}
        {a.pending_actions.length > 0 && <div><dt>Pending</dt><dd>{a.pending_actions.join(', ')}</dd></div>}
        {open && <div><dt>Cost</dt><dd>{usd(a.cost_micro_usd)}{a.model ? <span className="muted small"> · {a.model}</span> : null}</dd></div>}
      </dl>
      {a.errors.length > 0 && <ul className="naive-agent-errors">{a.errors.map((e) => <li key={e.id}>{time(e.at)} {e.summary}{e.detail ? `: ${e.detail}` : ''}</li>)}</ul>}
      {open && (<>
        <h4>Activity Timeline</h4>
        {a.timeline.length === 0 && <p className="muted small">No events recorded for this agent.</p>}
        <ol className="naive-timeline">
          {(a.history_truncated || a.catching_up) && <li className="muted small">{a.history_truncated ? 'Older events before this window are not loaded. ' : ''}{a.catching_up ? 'Newer events are still loading.' : ''}</li>}
          {a.timeline.map((e) => (
            <li key={e.id} className={`k-${e.kind}`}>
              <time title={full(e.at)}>{time(e.at)}</time>
              <span className="naive-kind">{KIND_LABEL[e.kind] ?? e.kind}</span>
              <div>
                <span>{e.from && e.to ? <b className="naive-route">{e.from} → {e.to}</b> : e.from ? <b className="naive-route">{e.from}</b> : e.to ? <b className="naive-route">to {e.to}</b> : null} {e.summary}</span>
                {e.detail && <details><summary>{e.truncated ? 'Details (truncated)' : 'Details'}</summary><p>{e.detail}</p></details>}
              </div>
            </li>
          ))}
        </ol>
      </>)}
      </div></td></tr>}
    </Fragment>
  );
}
