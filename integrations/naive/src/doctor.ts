import { naiveClient, safeError } from './client.js';

try {
  const client = naiveClient();
  const me = await client.auth.me();
  const checks = [];
  for (const read of [() => client.credits.subscription(), () => client.credits.show()]) {
    try { checks.push({ status: 'fulfilled' as const, value: await read() }); }
    catch (reason) { checks.push({ status: 'rejected' as const, reason }); }
  }
  console.log(JSON.stringify({ authenticated: true, identity: me,
    subscription: checks[0].status === 'fulfilled' ? checks[0].value : safeError(checks[0].reason),
    balance: checks[1].status === 'fulfilled' ? checks[1].value : safeError(checks[1].reason),
    approved_agent_limits: { monthly_usd: 10, per_task_usd: 1 },
    remote_resources_created: false }, (key, value) => /api_key|token|secret|email/i.test(key) ? '[REDACTED]' : value, 2));
  if (checks.some(c => c.status === 'rejected')) process.exitCode = 1;
} catch (error) { console.error(JSON.stringify(safeError(error))); process.exitCode = 1; }
