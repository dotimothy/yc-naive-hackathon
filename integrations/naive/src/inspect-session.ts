import { naiveClient, safeError } from './client.js';
const id = process.argv[2];
if (!id) throw new Error('Pass a Naive session ID.');
try {
  const client = naiveClient(); const s = await client.sessions.get(id);
  console.log(JSON.stringify({ session_id: s.id, status: s.status, stop_reason: s.stop_reason,
    spent_usd: s.consumed_micro_usd / 1_000_000, budget_usd: s.budget_micro_usd / 1_000_000 }));
  const events = await client.sessions.events(id, { limit: 100 });
  console.log(JSON.stringify(events.data.filter(e => ['message.completed', 'session.idle', 'session.error', 'session.usage'].includes(e.type)), null, 2));
} catch (error) { console.error(JSON.stringify(safeError(error))); process.exitCode = 1; }
