import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { setTimeout } from 'node:timers/promises';
import { createClient, ApiError } from '@usenaive-sdk/managed-agents';

/** Reuse the CLI's private local credential; never copy it into this repository. */
export function naiveClient(idempotencyKey?: string) {
  const profileName = process.env.VETTA_PROFILE || 'prod';
  let profile: { api_key?: string; base_url?: string } = {};
  const configFile = join(process.env.XDG_CONFIG_HOME || join(homedir(), '.config'), 'vetta', 'config.json');
  if (!process.env.NAIVE_API_KEY) {
    try {
      const saved = JSON.parse(readFileSync(configFile, 'utf8'));
      profile = saved.profiles?.[process.env.VETTA_PROFILE || saved.default_profile || profileName] || {};
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw new Error('Unable to read the local Naive CLI profile.');
    }
  }
  const apiKey = process.env.NAIVE_API_KEY || profile.api_key;
  if (!apiKey) throw new Error('Naive login required. Run the secure login command in integrations/naive/README.md.');
  const baseUrl = process.env.NAIVE_BASE_URL || profile.base_url || 'https://api.vetta.sh';
  if (!baseUrl.startsWith('https://')) throw new Error('Naive credentials require an HTTPS API origin.');
  return createClient({ baseUrl, apiKey,
    ...(idempotencyKey ? { idempotencyKey: () => idempotencyKey } : {}),
    fetch: async (input, init) => {
      // Only retry reads; never replay a paid write implicitly.
      for (let attempt = 0; ; attempt++) {
        let response: Response;
        try {
          response = await fetch(input, { ...init, signal: init?.signal ? AbortSignal.any([init.signal, AbortSignal.timeout(20000)]) : AbortSignal.timeout(20000) });
        } catch (error) {
          if ((init?.method || 'GET') !== 'GET' || attempt >= 2 || init?.signal?.aborted) throw error;
          await setTimeout(2 ** (attempt + 1) * 1000);
          continue;
        }
        if ((init?.method || 'GET') !== 'GET' || attempt >= 2 || ![429, 502, 503, 504].includes(response.status)) return response;
        const retry = response.headers.get('Retry-After');
        const seconds = retry ? (Number.isFinite(Number(retry)) ? Number(retry) : Math.max(0, (Date.parse(retry) - Date.now()) / 1000)) : 2 ** (attempt + 1);
        if (!Number.isFinite(seconds) || seconds > 10) return response;
        await response.body?.cancel();
        await setTimeout(Math.max(1, seconds) * 1000);
      }
    }
  });
}

export function safeError(error: unknown) {
  if (error instanceof ApiError) {
    return { status: error.status, code: error.code, request_id: error.requestId };
  }
  return { error: error instanceof Error ? error.message.replace(/sk_(?:live|test)_[\w.-]+/g, '[REDACTED]') : 'Operation failed' };
}
