import { localEvidence } from './local-evidence.js';
try { console.log(JSON.stringify({ ...await localEvidence(), remote_calls: 0, writes: 0 }, null, 2)); }
catch (error) { console.error(error instanceof Error ? error.message : 'Local check failed'); process.exitCode = 1; }
