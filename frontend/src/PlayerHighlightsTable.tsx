import { useState } from 'react';
import { PlayerStatsCard, clipStatCells } from './PlayerStatsCard';
import type { HighlightCandidate, HighlightPlayer } from './types';

export function PlayerHighlightsTable({ players, activeId, canGroup, onReview, onGroup, onSeek, candidateById }: {
  players: HighlightPlayer[]; activeId: string | null; canGroup: boolean;
  onReview: (id: string) => void; onGroup: (tracks: string[]) => void;
  onSeek: (ms: number, trackId?: string) => void; candidateById: Map<string, HighlightCandidate>;
}) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [detailId, setDetailId] = useState<string | null>(null);
  const matching = players.filter(p => `${p.label} ${p.track_ids.join(' ')}`.toLowerCase().includes(query.toLowerCase()));
  const pages = Math.max(1, Math.ceil(matching.length / 10));
  const current = Math.min(page, pages - 1);
  const detail = players.find(p => p.player_id === detailId);
  return <>
    <div className="player-table-toolbar">
      <label>Find player or track <input type="search" value={query} placeholder="Player name or track ID" onChange={e => { setQuery(e.target.value); setPage(0); }} /></label>
      <span className="muted small">Reviewed activity in this clip. — means unreviewed. Possession measures ball control.</span>
    </div>
    <div className="player-table-scroll" role="region" aria-label="Player highlights table" tabIndex={0}>
      <table className="player-table">
        <caption className="sr-only">Player groups, reviewed basketball stats and highlight candidates</caption>
        <thead><tr><th scope="col">Player / identity</th><th scope="col">Seen in clip</th>
          {['Possession', 'Passes', 'Shots (made/att.)', 'Rebounds'].map(label => <th scope="col" key={label}>{label}</th>)}
          <th scope="col">Candidates</th><th scope="col">Actions</th></tr></thead>
        <tbody>{matching.slice(current * 10, current * 10 + 10).map(p => <tr key={p.player_id} className={activeId === p.player_id ? 'on' : ''}>
          <th scope="row"><strong>{p.label}</strong><span className={`identity-label ${p.identity_confirmed ? 'confirmed' : ''}`}>{p.identity_confirmed ? 'Confirmed' : p.association_status === 'needs_review' ? 'Needs review' : 'Provisional'}</span></th>
          <td>{(Math.max(0, p.observed_ms) / 1000).toFixed(1)}s</td>
          {clipStatCells(p.basketball_stats).map(cell => <td key={cell.key} title={cell.sub}>{cell.value}</td>)}
          <td>{p.candidate_count}<span className="muted table-note">{p.selected_count} selected</span></td>
          <td><div className="table-actions"><button className="secondary small" onClick={() => onReview(p.player_id)}>Review Clips</button><button className="ghost small" aria-expanded={detailId === p.player_id} onClick={() => setDetailId(detailId === p.player_id ? null : p.player_id)}>Details</button></div></td>
        </tr>)}</tbody>
      </table>
      {!matching.length && <p className="hl-empty">No matching player groups.</p>}
    </div>
    <div className="table-pagination"><span>{matching.length ? `${current * 10 + 1}–${Math.min((current + 1) * 10, matching.length)} of ${matching.length} groups` : '0 groups'}</span>
      <button className="secondary small" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous Players</button>
      <button className="secondary small" disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>Next Players</button>
    </div>
    {detail && <div className="player-detail"><div className="panel-head"><h4>{detail.label} · evidence and identity</h4><button className="ghost small" onClick={() => setDetailId(null)}>Close Details</button></div>
      <PlayerStatsCard key={detail.player_id} player={detail} active={activeId === detail.player_id} onFilter={() => onReview(detail.player_id)} onGroup={detail.identity_confirmed ? undefined : () => onGroup(detail.track_ids)} canGroup={canGroup} onSeek={onSeek} candidateById={candidateById} />
    </div>}
  </>;
}
