import { useState } from 'react';
import { formatMs, trackColor } from './FilmPlayer';
import type { BasketballStats, HighlightCandidate, HighlightPlayer, RoleStatus, StatEvidence, TeamLabel } from './types';

// docs/REVIEWED_STATS_CONTRACT.md: every figure is a count within reviewer-approved evidence,
// never a full-game or official total, and every number links back to its source events.

const DASH = '—';
const EVIDENCE_PAGE = 8;

const EVIDENCE_LABEL: Record<string, string> = {
  pass: 'Completed pass',
  possession: 'Possession',
  made_shot: 'Made FG',
  missed_shot: 'Missed FG',
  made_free_throw: 'Made FT',
  missed_free_throw: 'Missed FT',
  assist: 'Assist',
  rebound: 'Rebound',
  steal: 'Steal',
  block: 'Block',
  turnover: 'Turnover',
};

type MetricKey = 'pass' | 'possession' | 'fg' | 'ft' | 'pts' | 'ast' | 'reb' | 'stl' | 'blk' | 'to';

const METRIC_EVENTS: Record<MetricKey, string[]> = {
  pass: ['pass'],
  possession: ['possession'],
  fg: ['made_shot', 'missed_shot'],
  ft: ['made_free_throw', 'missed_free_throw'],
  pts: ['made_shot', 'made_free_throw'],
  ast: ['assist'],
  reb: ['rebound'],
  stl: ['steal'],
  blk: ['block'],
  to: ['turnover'],
};

const METRIC_NAME: Record<MetricKey, string> = {
  pass: 'Passes',
  possession: 'Possession time',
  fg: 'Field goals',
  ft: 'Free throws',
  pts: 'Points',
  ast: 'Assists',
  reb: 'Rebounds',
  stl: 'Steals',
  blk: 'Blocks',
  to: 'Turnovers',
};

type Cell = { key: MetricKey; label: string; value: string; sub?: string };

const num = (n: unknown): number => (typeof n === 'number' && Number.isFinite(n) ? n : 0);

function shooting(made: number, att: number): { value: string; sub: string } {
  if (att <= 0) return { value: DASH, sub: 'no attempts' };
  return { value: `${made}/${att}`, sub: `${Math.round((made / att) * 100)}% of reviewed` };
}

export function statCells(s: BasketballStats | null | undefined): Cell[] {
  const live = !!s?.has_evidence;
  const count = (n: unknown) => (live ? String(num(n)) : DASH);
  const fg = live ? shooting(num(s!.field_goals_made), num(s!.field_goals_attempted)) : { value: DASH, sub: '' };
  const ft = live ? shooting(num(s!.free_throws_made), num(s!.free_throws_attempted)) : { value: DASH, sub: '' };
  let pts: { value: string; sub?: string } = { value: DASH };
  if (live) {
    pts =
      typeof s!.points === 'number' && Number.isFinite(s!.points)
        ? { value: String(s!.points) }
        : { value: DASH, sub: num(s!.unvalued_makes) > 0 ? `shot value needed (${num(s!.unvalued_makes)})` : 'shot value needed' };
  }
  return [
    { key: 'pts', label: 'PTS', ...pts },
    { key: 'fg', label: 'FG', ...fg },
    { key: 'ft', label: 'FT', ...ft },
    { key: 'ast', label: 'AST', value: count(s?.assists) },
    { key: 'reb', label: 'REB', value: count(s?.rebounds) },
    { key: 'stl', label: 'STL', value: count(s?.steals) },
    { key: 'blk', label: 'BLK', value: count(s?.blocks) },
    { key: 'to', label: 'TO', value: count(s?.turnovers) },
  ];
}

/** Short-clip activity uses reviewed events; visibility is never ball possession. */
export function clipStatCells(s: BasketballStats | null | undefined): Cell[] {
  const detailed = statCells(s);
  return [
    { key: 'possession', label: 'Possession', value: typeof s?.possession_ms === 'number' ? `${(s.possession_ms / 1000).toFixed(1)}s` : DASH, sub: 'Reviewed ball-control intervals' },
    { key: 'pass', label: 'Passes', value: typeof s?.passes === 'number' ? String(s.passes) : DASH, sub: 'Reviewed completed passes' },
    { ...detailed.find(c => c.key === 'fg')!, label: 'Shots', sub: 'Made / attempted · reviewed' },
    { ...detailed.find(c => c.key === 'reb')!, label: 'Rebounds' },
  ];
}

/** Plain-language explanation of why a reviewed event is not in the totals. */
export function heldReasonText(reason: string): string {
  // Backend sentences are already plain language; only translate bare codes.
  if (/\s/.test(reason.trim())) return reason.trim();
  const r = reason.toLowerCase();
  if (r.includes('duplicate') || r.includes('distinct')) return 'another event of the same type is within 1.5 s — confirm they are different plays.';
  if (r.includes('actor') || r.includes('attribution') || r.includes('association') || r.includes('identity'))
    return 'nobody has confirmed this player made the play (automatic player IDs are not enough).';
  if (r.includes('out_of_range') || r.includes('outside') || r.includes('bounds')) return "the saved event time is outside the candidate's source range.";
  if (r.includes('time')) return 'no source event time has been saved.';
  if (r.includes('value') || r.includes('points')) return 'the made field goal has no point value; it counts toward FG but not points.';
  if (r.includes('type') || r.includes('visual')) return 'the approval has no basketball outcome type.';
  return reason.replace(/_/g, ' ');
}

function TeamRoleBadges({ team, role }: { team?: TeamLabel | null; role?: RoleStatus | null }) {
  return (
    <>
      {team && team !== 'unknown' && (
        <span className="hl-badge team" title="Anonymous colour grouping by first appearance. Heuristic — not a verified team.">
          {team}? · colour group
        </span>
      )}
      {team === 'unknown' && <span className="hl-badge" title="Colour grouping could not assign a group">Team unknown</span>}
      {role === 'likely_non_player' && (
        <span className="hl-badge held" title="Several signals suggest a referee, coach or spectator. Review the footage; nothing is removed automatically.">
          Possible non-player · review
        </span>
      )}
      {role === 'review_required' && (
        <span className="hl-badge held" title="One signal suggests this may not be a player. Review the footage.">
          Role needs review
        </span>
      )}
    </>
  );
}

export { TeamRoleBadges };

export function PlayerStatsCard(props: {
  player: HighlightPlayer;
  active: boolean;
  onFilter: () => void;
  onGroup?: () => void;
  canGroup: boolean;
  onSeek: (ms: number, trackId?: string) => void;
  candidateById: Map<string, HighlightCandidate>;
}) {
  const { player: p, active, onFilter, onGroup, canGroup, onSeek, candidateById } = props;
  const stats = p.basketball_stats ?? null;
  const [metric, setMetric] = useState<MetricKey | 'all' | null>(null);
  const [limit, setLimit] = useState(EVIDENCE_PAGE);
  const evidence: StatEvidence[] = stats?.evidence ?? [];
  const held = stats?.held_events ?? [];

  const evidenceFor = (m: MetricKey | 'all') => (m === 'all' ? evidence : evidence.filter((e) => METRIC_EVENTS[m].includes(e.event_type)));
  const shown = metric ? [...evidenceFor(metric)].sort((a, b) => a.event_time_ms - b.event_time_ms) : [];
  const pick = (m: MetricKey | 'all') => {
    setMetric(metric === m ? null : m);
    setLimit(EVIDENCE_PAGE);
  };

  const status = p.identity_confirmed
    ? 'Reviewer-confirmed group'
    : p.association_status === 'needs_review'
    ? 'Association needs review'
    : p.identity_source === 'automatic'
    ? 'Automatic player ID · provisional'
    : 'Temporary track · provisional';

  return (
    <article className={`hl-player ${p.identity_confirmed ? 'confirmed' : 'provisional'}${active ? ' on' : ''}`}>
      <header>
        <strong title={p.player_id}>{p.label}</strong>
        <span className={`hl-badge ${p.identity_confirmed ? 'ok' : ''}`}>{p.identity_confirmed ? 'Confirmed' : 'Provisional'}</span>
      </header>

      <div className="hl-assoc">
        <span className="muted small">
          {status}
          {!p.identity_confirmed && typeof p.association_confidence === 'number' && Number.isFinite(p.association_confidence)
            ? ` · match ${p.association_confidence.toFixed(2)}`
            : ''}
        </span>
        <TeamRoleBadges team={p.team_label} role={p.role_status} />
      </div>
      <div className="hl-tracks">
        {p.track_ids.map((t) => (
          <code key={t} style={{ ['--c' as string]: trackColor(String(t)) }}>T{t}</code>
        ))}
      </div>

      {!stats ? (
        <p className="hl-stat-empty">Reviewed basketball stats unavailable for this data.</p>
      ) : (
        <>
          <div className="hl-statline" role="group" aria-label={`Reviewed basketball stat line for ${p.label}`}>
            {clipStatCells(stats).map((c) => {
              const support = stats.has_evidence ? evidenceFor(c.key).length : 0;
              return (
                <button
                  key={c.key}
                  type="button"
                  className={`hl-stat${metric === c.key ? ' on' : ''}${c.value === DASH ? ' empty' : ''}`}
                  aria-pressed={metric === c.key}
                  disabled={support === 0}
                  title={support ? `${METRIC_NAME[c.key]}: show ${support} supporting source event${support === 1 ? '' : 's'}` : `${METRIC_NAME[c.key]}: no reviewed evidence`}
                  onClick={() => pick(c.key)}
                >
                  <small>{c.label}</small>
                  <b>{c.value}</b>
                  {c.sub && <i>{c.sub}</i>}
                </button>
              );
            })}
          </div>
          <details><summary>More reviewed stats</summary><dl className="metrics">{statCells(stats).map(c => <div key={c.key}><dt>{c.label}</dt><dd>{c.value}</dd></div>)}</dl></details>
          <p className="hl-sample small">
            {stats.has_evidence ? (
              <>
                From{' '}
                <button type="button" className="link inline" onClick={() => pick('all')} disabled={!evidence.length}>
                  {num(stats.reviewed_events_count)} reviewed event{num(stats.reviewed_events_count) === 1 ? '' : 's'}
                </button>
                {' '}· counts within reviewed clips only, not full-game totals.
              </>
            ) : (
              <>No reviewed basketball events yet.</>
            )}
          </p>
          {(num(stats.pending_candidates) > 0 || num(stats.held_events_count) > 0) && (
            <p className="hl-partial small">
              Partial review:{' '}
              {num(stats.pending_candidates) > 0 && <>{num(stats.pending_candidates)} candidate{num(stats.pending_candidates) === 1 ? '' : 's'} pending</>}
              {num(stats.pending_candidates) > 0 && num(stats.held_events_count) > 0 && ' · '}
              {num(stats.held_events_count) > 0 && <>{num(stats.held_events_count)} approved event{num(stats.held_events_count) === 1 ? '' : 's'} held from totals</>}
            </p>
          )}

          {metric && (
            <div className="hl-evidence">
              <h5>{metric === 'all' ? 'All counted evidence' : `${METRIC_NAME[metric]} evidence`} ({shown.length})</h5>
              <ul>
                {shown.slice(0, limit).map((e) => (
                  <li key={`${e.candidate_id}:${e.event_type}:${e.event_time_ms}`}>
                    <button type="button" className="ghost small" onClick={() => onSeek(e.event_time_ms, String(e.track_id))} title="Seek film to the source event and focus its track">
                      ▶ {formatMs(e.event_time_ms)}{e.possession_end_ms != null ? ` – ${formatMs(e.possession_end_ms)}` : ''}
                    </button>
                    <span>{EVIDENCE_LABEL[e.event_type] ?? e.event_type.replace(/_/g, ' ')}</span>
                    {e.event_type === 'made_shot' && <span className="muted">{e.points === null ? 'value unknown' : `+${e.points}`}</span>}
                    {e.event_type === 'made_free_throw' && <span className="muted">+1</span>}
                    <code style={{ ['--c' as string]: trackColor(String(e.track_id)) }}>T{e.track_id}</code>
                    {e.clip_uri && (
                      <a href={e.clip_uri} target="_blank" rel="noreferrer">clip ↗</a>
                    )}
                  </li>
                ))}
              </ul>
              {shown.length > limit && (
                <button type="button" className="link" onClick={() => setLimit((n) => n + EVIDENCE_PAGE)}>
                  Show {Math.min(EVIDENCE_PAGE, shown.length - limit)} More
                </button>
              )}
            </div>
          )}

          {held.length > 0 && (
            <details className="hl-held-list">
              <summary>Held from totals ({held.length})</summary>
              <ul>
                {held.slice(0, 20).map((h) => {
                  const c = candidateById.get(h.candidate_id);
                  const t = c ? (typeof c.review?.event_time_ms === 'number' ? c.review.event_time_ms : c.action_start_ms) : null;
                  return (
                    <li key={h.candidate_id}>
                      {c && t !== null ? (
                        <button type="button" className="ghost small" onClick={() => onSeek(t, String(c.track_id))}>
                          ▶ {formatMs(t)}
                        </button>
                      ) : (
                        <code>{h.candidate_id.slice(0, 8)}</code>
                      )}
                      <span>{heldReasonText(h.reason)}</span>
                    </li>
                  );
                })}
              </ul>
              {held.length > 20 && <p className="muted small">+{held.length - 20} more; filter candidates to review them.</p>}
            </details>
          )}
        </>
      )}

      <dl className="metrics hl-footage">
        <div><dt>Observed sample duration</dt><dd>{(Math.max(0, p.observed_ms) / 1000).toFixed(1)} s</dd></div>
        <div><dt>Clip candidates</dt><dd>{p.candidate_count}</dd></div>
        <div><dt>Selected for render</dt><dd>{p.selected_count}</dd></div>
      </dl>
      <div className="hl-card-actions">
        <button className="secondary small" onClick={onFilter}>{active ? 'Show All Candidates' : 'Review Candidates'}</button>
        {onGroup && (
          <button className="ghost small" onClick={onGroup} disabled={!canGroup} title="Add these track IDs to the group form">
            Add to Group Form
          </button>
        )}
      </div>
    </article>
  );
}
