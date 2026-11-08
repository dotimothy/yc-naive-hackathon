import test from 'node:test';
import assert from 'node:assert/strict';
import { naiveClient, safeError } from './client.js';
import { agentSpec } from './agent-spec.js';
import { localEvidence } from './local-evidence.js';

function environment() {
  const key = process.env.NAIVE_API_KEY, url = process.env.NAIVE_BASE_URL, fetcher = globalThis.fetch;
  process.env.NAIVE_API_KEY = 'unit-test-credential';
  process.env.NAIVE_BASE_URL = 'https://naive.example.test';
  return () => { globalThis.fetch = fetcher; if (key === undefined) delete process.env.NAIVE_API_KEY; else process.env.NAIVE_API_KEY = key; if (url === undefined) delete process.env.NAIVE_BASE_URL; else process.env.NAIVE_BASE_URL = url; };
}

test('retry a rate-limited read, with bounded attempts', async () => {
  const restore = environment(); let calls = 0;
  try {
    globalThis.fetch = async () => ++calls === 1 ? new Response('{}', { status: 429, headers: { 'Retry-After': '0' } }) : Response.json({ object: 'credit_balance', balance_micro_usd: 123, mode: 'test' });
    assert.equal((await naiveClient().credits.show()).balance_micro_usd, 123); assert.equal(calls, 2);
  } finally { restore(); }
});

test('a write is not automatically retried', async () => {
  const restore = environment(); let calls = 0;
  try {
    globalThis.fetch = async () => { calls++; return Response.json({ error: { code: 'rate_limited', type: 'quota', message: 'busy', request_id: 'req_unit_test' } }, { status: 429 }); };
    await assert.rejects(naiveClient().agents.create(agentSpec)); assert.equal(calls, 1);
  } finally { restore(); }
});

test('reject an insecure Naive origin before sending credentials', () => {
  const restore = environment();
  try { process.env.NAIVE_BASE_URL = 'http://example.test'; assert.throws(() => naiveClient(), /HTTPS/); }
  finally { restore(); }
});

test('error output redacts credential-like strings', () => {
  assert.deepEqual(safeError(new Error('bad sk_live_unit_test_placeholder')), { error: 'bad [REDACTED]' });
});

test('evidence ignores older successful jobs when the latest job failed', async () => {
  const restore = environment(); const methods: string[] = [];
  try {
    globalThis.fetch = async (input, init) => {
      methods.push(init?.method || 'GET'); const path = new URL(String(input)).pathname;
      if (path === '/api/health') return Response.json({ status: 'ok' });
      if (path === '/api/games') return Response.json({ items: [{ game_id: 'current' }, { game_id: 'stale' }] });
      if (path === '/api/games/stale/jobs') return Response.json({ items: [{ job_id: 'old', status: 'succeeded' }, { job_id: 'failed', status: 'failed' }] });
      if (path === '/api/games/current/jobs') return Response.json({ items: [{ job_id: 'new', status: 'succeeded', report: {} }] });
      if (path === '/api/games/current/clips') return Response.json({ items: [] });
      throw new Error('Unexpected request: ' + path);
    };
    const evidence = await localEvidence(); assert.equal(evidence.result.job_id, 'new'); assert.ok(methods.every(m => m === 'GET'));
  } finally { restore(); }
});
