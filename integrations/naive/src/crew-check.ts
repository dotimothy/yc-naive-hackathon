import { naiveClient, safeError } from './client.js';
import { crew, crewMembers, validateCrew } from './crew.js';
import { localEvidence } from './local-evidence.js';
try {
  const client = naiveClient();
  const agents = [];
  let after: string | undefined;
  for (let page = 0; ; page++) {
    if (page >= 10) throw new Error('Crew inventory pagination incomplete');
    const result = await client.agents.list({ limit: 100, ...(after ? { after } : {}) });
    agents.push(...result.data);
    if (!result.has_more) break;
    if (!result.next_cursor || result.next_cursor === after) throw new Error('Invalid inventory cursor');
    after = result.next_cursor;
  }
  validateCrew(agents);
  const ids = new Set<string>([crew.coordinator.id, ...crewMembers.map(a => a.id)]);
  console.log(JSON.stringify({ crew, status: 'verified', mode: 'read_only',
    budgets: agents.filter(a => ids.has(a.id)).map(a => ({ id: a.id, name: a.name, budget: a.budget })),
    execution_enabled: false, publishing_enabled: false,
    blockers: ['Scoped local API bridge not connected', 'Existing crew budgets exceed integration allocation; bounded dispatch required'],
  }, null, 2));
  if (process.argv.includes('--evidence')) {
    const evidence = await localEvidence();
    console.log(JSON.stringify({ type: 'analysis_handoff_preview', recipient: crew.egress[0].id,
      evidence: evidence.result, dispatched: false,
      instruction: 'Use only supplied evidence. No visual inspection or verified identity is established. Do not invent outcomes or publish.'
    }, null, 2));
  }
} catch (error) {
  console.error(JSON.stringify(safeError(error)));
  process.exitCode = 1;
}
