import { join } from 'node:path';
import { ModelRuntime } from '@earendil-works/pi-coding-agent';
import { groupModels, streamProvider } from './provider-adapter.mjs';

// Register the endpoint's public catalogue while preserving native model identities
// inside Provider messages. Pi uses the public route for every request, including compaction.
export async function agentModel({ config, agentDir, provider: providerClient, signal, event, beforeRequest }) {
  const catalogue = await providerClient.listModels({}, { signal, timeoutMs: 10000 });
  const groups = groupModels(catalogue.models, { onInvalid: message => event('agent.model_unavailable', { message }) });
  const modelRuntime = await ModelRuntime.create({ authPath: join(agentDir, 'auth.json'), modelsPath: null, allowModelNetwork: false, signal });
  let selected;
  for (const [provider, routes] of groups) {
    modelRuntime.registerProvider(provider, {
      name: provider, api: 'cyclo-pi', baseUrl: 'http://dcomp', apiKey: 'dcomp', authHeader: false,
      models: routes.map(r => r.model),
      streamSimple(model, context, options = {}) {
        signal.throwIfAborted();
        options.signal?.throwIfAborted();
        const route = routes.find(r => r.model.id === model.id);
        if (!route) throw new Error('Pi selected a model outside the Provider catalogue');
        beforeRequest(context);
        // The timeout bounds one inference attempt. Waiting for an exhausted
        // provider to come back is not work the agent can hurry, so it runs
        // outside that budget until the job is cancelled.
        return streamProvider(providerClient, route.publicId, { ...model, api: 'cyclo-pi' }, context,
          { ...options, ...config.options,
            signal: AbortSignal.any([signal, ...(options.signal ? [options.signal] : [])]),
            attemptTimeoutMs: (config.timeoutSeconds ?? 600) * 1000 },
          { onExhaustion: ({ retryAt, delayMs }) => event('agent.provider_exhausted', { retryAt: retryAt.toISOString(), delayMs }),
            onRetry: () => event('agent.provider_retrying', {}) });
      },
    });
    for (const route of routes) if (route.publicId === config.model) selected = modelRuntime.getModel(provider, route.model.id);
  }
  if (!selected) throw new Error(`Provider has no usable model ${config.model}`);
  return { modelRuntime, model: selected };
}
