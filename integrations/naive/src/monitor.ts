import { mkdir, readFile, writeFile, appendFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { setTimeout } from 'node:timers/promises';
import { naiveClient, safeError } from './client.js';

const folder = fileURLToPath(new URL('../../../data/naive/', import.meta.url));
const watch = process.argv.includes('--watch');
const client = naiveClient();
await mkdir(folder, { recursive: true });
do {
  try {
    const balance = await client.credits.show();
    const spend = await client.credits.spend();
    let agentSpend = null;
    try {
      const state = JSON.parse(await readFile(folder + 'agent.json', 'utf8'));
      agentSpend = await client.agents.spend(state.agent_id, { by: 'component' });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    const observation = { balance_usd: balance.balance_micro_usd / 1_000_000, agent_spend: agentSpend, observed_at: new Date().toISOString(), balance, spend,
      approved_agent_limits: { monthly_usd: 10, per_task_usd: 1 },
      note: 'Read-only observations. Naive enforces actual agent budgets server-side.' };
    const line = JSON.stringify(observation);
    await writeFile(folder + 'spend-latest.json', line + '\n', { mode: 0o600 });
    await appendFile(folder + 'spend-history.jsonl', line + '\n', { mode: 0o600 });
    await writeFile(folder + 'monitor-health.json', JSON.stringify({ observed_at: observation.observed_at, ok: true }) + '\n', { mode: 0o600 });
    console.log(JSON.stringify({ observed_at: observation.observed_at, balance_usd: observation.balance_usd, agent_spend: agentSpend, organization_spent_usd: spend.spent_micro_usd / 1_000_000, approved_agent_limits: observation.approved_agent_limits }));
  } catch (error) {
    const failure = { observed_at: new Date().toISOString(), ok: false, ...safeError(error) };
    await writeFile(folder + 'monitor-health.json', JSON.stringify(failure) + '\n', { mode: 0o600 });
    console.error(JSON.stringify(failure));
    if (!watch) process.exitCode = 1;
  }
  if (watch) await setTimeout(60000);
} while (watch);
