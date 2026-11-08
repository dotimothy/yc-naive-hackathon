import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { setTimeout } from 'node:timers/promises';
import { naiveClient, safeError } from './client.js';
import { localEvidence } from './local-evidence.js';

const folder = fileURLToPath(new URL('../../../data/naive/', import.meta.url));
const client = naiveClient();
let runningId: string | undefined;
try {
  const state = JSON.parse(await readFile(folder + 'agent.json', 'utf8'));
  const agent = await client.agents.get(state.agent_id);
  if (agent.budget.max_task_micro_usd !== 1_000_000 || agent.budget.cap_micro_usd !== 10_000_000 || agent.tools.default_config.permission !== 'deny' || Object.keys(agent.tools.configs).length) throw new Error('Agent configuration changed; smoke test refused.');
  const evidence = await localEvidence();
  const before = await client.credits.show();
  // Stable key: rerunning this onboarding smoke for the same local job cannot create another paid session.
  const session = await client.sessions.create({ agent_id: agent.id, budget_micro_usd: 1_000_000,
    message: 'SDK onboarding smoke test, not video analysis. Read ONLY the supplied JSON. Return exactly one JSON object containing job_id, status, clip_count and identity_verified copied from result. Do not use tools or ask questions. Evidence: ' + JSON.stringify(evidence),
  }, { idempotencyKey: 'playlattice-sdk-smoke-v1-' + evidence.result.job_id });
  runningId = session.id;
  console.log(JSON.stringify({ session_id: session.id, phase: 'waiting', task_cap_usd: 1 }));
  const deadline = Date.now() + 180000;
  let terminal = session;
  while (Date.now() < deadline) {
    terminal = await client.sessions.get(session.id);
    if (['idle', 'completed', 'failed', 'cancelled'].includes(terminal.status)) break;
    await setTimeout(2000);
  }
  if (!['idle', 'completed', 'failed', 'cancelled'].includes(terminal.status)) {
    await client.sessions.cancel(session.id); runningId = undefined;
    throw new Error('Smoke timeout; cancellation requested.');
  }
  runningId = undefined;
  const events = await client.sessions.events(session.id, { limit: 100 });
  const messages = events.data.filter(e => e.type === 'message.completed' && e.data.role === 'assistant').map(e => e.data);
  const completedTurn = events.data.some(e => e.type === 'session.idle' && e.data.stop_reason === 'end_turn');
  const expected = { job_id: evidence.result.job_id, status: evidence.result.status, clip_count: evidence.result.clip_count, identity_verified: false };
  const output = messages.at(-1);
  const actual = output ? JSON.parse(String(output.content).replace(/^```(?:json)?\s*|\s*```$/g, '')) : null;
  const matches = actual && Object.entries(expected).every(([key, value]) => actual[key] === value);
  const after = await client.credits.show();
  const agentSpend = await client.agents.spend(agent.id, { by: 'component' });
  const record = { session_id: session.id, local_job_id: evidence.result.job_id,
    status: terminal.status, stop_reason: terminal.stop_reason,
    session_spend_micro_usd: terminal.consumed_micro_usd,
    session_spend_usd: terminal.consumed_micro_usd / 1_000_000,
    balance_before: before, balance_after: after, agent_spend: agentSpend,
    messages, expected_metrics_match: Boolean(matches), completed_turn_observed: completedTurn, note: 'SDK orchestration smoke using supplied real local metrics; no PlayLattice tools/remote GPU bridge connected.' };
  await writeFile(folder + 'smoke-latest.json', JSON.stringify(record, null, 2), { mode: 0o600 });
  console.log(JSON.stringify(record, null, 2));
  if (!completedTurn || !matches) throw new Error('Naive smoke did not finish successfully. Read data/naive/smoke-latest.json.');
} catch (error) {
  if (runningId) {
    try {
      const status = await client.sessions.get(runningId);
      if (['queued', 'running'].includes(status.status)) await client.sessions.cancel(runningId);
    } catch { console.error('Session status is unconfirmed; inspect it before retrying. Stable smoke idempotency prevents duplicate session creation.'); }
  }
  console.error(JSON.stringify(safeError(error))); process.exitCode = 1;
}
