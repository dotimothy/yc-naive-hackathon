/** Read completed current-job metrics only. Does not trigger analysis or expose media bytes. */
export async function localEvidence() {
  const base = process.env.PLAYLATTICE_URL || 'http://127.0.0.1:8000';
  const read = async (path: string) => {
    const response = await fetch(new URL(path, base), { signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  };
  const health = await read('/api/health');
  const games = await read('/api/games');
  for (const game of [...games.items].reverse()) {
    const jobs = await read(`/api/games/${encodeURIComponent(game.game_id)}/jobs`);
    const job = jobs.items.at(-1);
    if (job?.status !== 'succeeded') continue;
    const clips = await read(`/api/games/${encodeURIComponent(game.game_id)}/clips`);
    return { health, result: { game_id: game.game_id, job_id: job.job_id, status: job.status,
      model: job.report?.model, device: job.report?.device, source_sha256: job.report?.source_sha256,
      processed_frames: job.report?.processed_frames, track_samples: job.report?.track_samples,
      elapsed_seconds: job.report?.elapsed_seconds, clip_count: clips.items.length,
      identity_verified: false, context: 'Supplied structured metadata only; no video content was inspected.' } };
  }
  throw new Error('No completed current analysis found. Submit an authorized sample first.');
}
