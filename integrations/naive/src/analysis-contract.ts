import { createHash } from 'node:crypto';
export type ClipEvidence = { clip_id: string; source_start_ms: number; source_end_ms: number; shot_id: string; identity_verified: false };
export type AnalysisEvidence = { schema_version: 1; game_id: string; job_id: string; source_sha256: string; clips: ClipEvidence[]; identity_verified: false; visual_input: false };
export const packageSchema = {
  type: 'object', additionalProperties: false,
  properties: {
    job_id: { type: 'string' }, source_sha256: { type: 'string' },
    clips: { type: 'array', maxItems: 5, items: { type: 'object', additionalProperties: false,
      properties: { clip_id: { type: 'string' }, caption: { type: 'string', maxLength: 280 }, reason: { type: 'string', maxLength: 400 } }, required: ['clip_id', 'caption', 'reason'] } },
    warnings: { type: 'array', items: { type: 'string', maxLength: 400 }, maxItems: 10 },
    published: { const: false }, identity_verified: { const: false }, visual_input: { const: false },
  }, required: ['job_id', 'source_sha256', 'clips', 'warnings', 'published', 'identity_verified', 'visual_input'],
};
export function snapshotId(evidence: AnalysisEvidence) { return createHash('sha256').update(JSON.stringify(evidence)).digest('hex'); }
export function validatePackage(value: unknown, evidence: AnalysisEvidence) {
  const p = value as Record<string, unknown> | null;
  if (!p || p.job_id !== evidence.job_id || p.source_sha256 !== evidence.source_sha256 || p.published !== false || p.identity_verified !== false || p.visual_input !== false) throw new Error('Output provenance or evidence flags do not match');
  if (!Array.isArray(p.clips) || p.clips.length > 5 || !Array.isArray(p.warnings) || p.warnings.length > 10 || p.warnings.some(w => typeof w !== 'string' || w.length > 400)) throw new Error('Invalid package shape');
  const known = new Map(evidence.clips.map(c => [c.clip_id, c]));
  const selected: ClipEvidence[] = []; const seen = new Set<string>();
  for (const clip of p.clips) {
    if (!clip || typeof clip.clip_id !== 'string' || !known.has(clip.clip_id) || seen.has(clip.clip_id) || typeof clip.caption !== 'string' || !clip.caption.trim() || clip.caption.length > 280 || typeof clip.reason !== 'string' || !clip.reason.trim() || clip.reason.length > 400) throw new Error('Unknown, duplicate or invalid output clip');
    const source = known.get(clip.clip_id)!;
    if (selected.some(c => Math.max(c.source_start_ms, source.source_start_ms) < Math.min(c.source_end_ms, source.source_end_ms))) throw new Error('Output clips overlap');
    selected.push(source); seen.add(clip.clip_id);
  }
  if (selected.reduce((sum,c)=>sum+c.source_end_ms-c.source_start_ms,0)>60000) throw new Error('Total output exceeds sixty seconds');
  return p;
}
/** Strip scores, person identifiers, media URLs and prose: metadata is not visual proof. */
export function buildEvidence(gameId: string, job: any, clips: any[]): AnalysisEvidence {
  const hash = job.report?.source_sha256;
  if (job.status !== 'succeeded' || job.game_id !== gameId || typeof job.job_id !== 'string' || !/^[a-f0-9]{64}$/.test(hash)) throw new Error('Completed source-bound analysis required');
  const ids = new Set<string>();
  const safe = clips.filter(c => c.game_id === gameId && c.job_id === job.job_id && c.source_sha256 === hash && c.qa_status === 'passed' && c.decoded === true && typeof c.clip_id === 'string' && typeof c.shot_id === 'string' && Number.isSafeInteger(c.source_start_ms) && Number.isSafeInteger(c.source_end_ms) && c.source_start_ms >= 0 && c.source_end_ms > c.source_start_ms && c.source_end_ms - c.source_start_ms <= 15000)
    .sort((a,b) => a.source_start_ms-b.source_start_ms || a.clip_id.localeCompare(b.clip_id))
    .filter(c => { if (ids.has(c.clip_id)) return false; ids.add(c.clip_id); return true; }).slice(0, 20)
    .map(c => ({ clip_id: c.clip_id, source_start_ms: c.source_start_ms, source_end_ms: c.source_end_ms, shot_id: c.shot_id, identity_verified: false as const }));
  return { schema_version: 1, game_id: gameId, job_id: job.job_id, source_sha256: hash, clips: safe, identity_verified: false, visual_input: false };
}
