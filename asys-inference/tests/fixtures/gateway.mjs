// Exercise the installed CLI, native Pi login, persistence and reload without
// contacting an external service or using real credentials.
import { runGateway } from './main.mjs';
import { createGatewayServices } from './services.mjs';

const provider = {
  id: 'test', name: 'Test provider', baseUrl: 'https://example.invalid',
  auth: {
    apiKey: {
      async login(interaction) {
        return { type: 'api_key', key: await interaction.prompt({ type: 'secret', message: 'Enter test key' }) };
      },
      async resolve({ credential }) { return credential?.key ? { auth: { apiKey: credential.key } } : undefined; },
    },
    oauth: {
      async login(interaction) {
        interaction.notify({ type: 'auth_url', url: 'https://example.invalid/authorize' });
        const mode = await interaction.prompt({ type: 'select', message: 'Authorize', options: [{ id: 'code', label: 'Manual code' }, { id: 'callback', label: 'Browser callback' }] });
        if (mode === 'code') {
          if (await interaction.prompt({ type: 'manual_code', message: 'Paste authorization code' }) !== 'test-code') throw new Error('Wrong test code');
        } else {
          const cancel = new AbortController();
          const prompt = interaction.prompt({ type: 'manual_code', message: 'Optional code', signal: cancel.signal });
          setTimeout(() => cancel.abort(), 100);
          try { await prompt; } catch (error) { if (error.name !== 'AbortError') throw error; }
        }
        return { type: 'oauth', access: 'test-access', refresh: 'test-refresh', expires: Date.now() + 3600000 };
      },
      async refresh(value) { return value; },
      async toAuth(value) { return { apiKey: value.access }; },
    },
  },
  getModels() { return [{ id: 'model', provider: 'test', api: 'openai-responses', input: ['text'], contextWindow: 4096, maxTokens: 1024 }]; },
  stream() {}, streamSimple() {},
};

await runGateway({ createServices: options => createGatewayServices({ ...options, providers: [provider] }) });
