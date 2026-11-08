import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { naiveClient, safeError } from './client.js';
import { agentSpec } from './agent-spec.js';

try {
  const client = naiveClient('playlattice-clip-curator-onboard-v1');
  const identity = await client.auth.me();
  const subscription = await client.credits.subscription();
  const balance = await client.credits.show();
  console.log(JSON.stringify({ plan_status: subscription.status, balance,
    proposed_agent: agentSpec.name, model: agentSpec.model, budget: agentSpec.budget,
    tools: 'none connected; deny by default', creates_session: false }, null, 2));
  if (subscription.status !== 'active') throw new Error('Naive plan is not active; operator account setup is required.');
  if (balance.balance_micro_usd <= 0) throw new Error('Naive balance is empty; no resources created.');
  await client.models.retrieve(agentSpec.model);
  let found;
  let after: string | undefined;
  for (let page = 0; page < 10; page++) {
    const list = await client.agents.list({ limit: 100, ...(after ? { after } : {}) });
    found = list.data.find(a => a.name === agentSpec.name);
    if (found || !list.has_more) break;
    after = list.next_cursor || undefined;
    if (page === 9) throw new Error('Agent listing exceeded the safety page limit.');
  }
  if (found && (found.budget.cap_micro_usd !== agentSpec.budget.cap_micro_usd || found.budget.max_task_micro_usd !== agentSpec.budget.max_task_micro_usd || found.budget.period !== agentSpec.budget.period || found.model !== agentSpec.model)) {
    throw new Error('An existing agent has different settings; no existing configuration was changed.');
  }
  if (found && (found.tools.default_config.permission !== 'deny' || Object.keys(found.tools.configs).length > 0)) throw new Error('Existing agent tool policy differs; no configuration changed.');
  if (!process.argv.includes('--apply')) {
    console.log(JSON.stringify({ dry_run: true, action: found ? 'reuse' : 'create', existing_id: found?.id }));
  } else {
    const agent = found || await client.agents.create(agentSpec);
    const verified = await client.agents.get(agent.id);
    if (verified.budget.cap_micro_usd !== 10_000_000 || verified.budget.max_task_micro_usd !== 1_000_000 || verified.budget.period !== 'month') throw new Error('Server budget verification failed.');
    const folder = fileURLToPath(new URL('../../../data/naive/', import.meta.url));
    await mkdir(folder, { recursive: true });
    await writeFile(folder + 'agent.json', JSON.stringify({ agent_id: agent.id, name: agent.name,
      model: verified.model, budget: verified.budget, verified_at: new Date().toISOString(),
      identity, balance_before: balance }, (key, value) => /api_key|token|secret|email/i.test(key) ? '[REDACTED]' : value, 2), { mode: 0o600 });
    console.log(JSON.stringify({ agent_id: agent.id, created: !found, server_budget: verified.budget, sessions_started: 0 }));
  }
} catch (error) { console.error(JSON.stringify(safeError(error))); process.exitCode = 1; }
