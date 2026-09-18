import { parseArgs } from 'node:util';
import { dirname, join, resolve } from 'node:path';
import { runAgent } from './agent-session.mjs';
import { providerClient } from './provider.mjs';
import { writeJSON } from './files.mjs';
import { requiredString } from './values.mjs';
import { agentDefinition } from './agent-definition.mjs';

export async function agent({ job, argv, env, signal }, { provider = providerClient(env), definition: suppliedDefinition, event } = {}) {
  const { values, positionals } = parseArgs({ args: argv, allowPositionals: true, options: {
    model: { type: 'string' }, agent: { type: 'string' }, extension: { type: 'string', multiple: true },
    'max-steps': { type: 'string' }, timeout: { type: 'string' },
  } });
  const input = job.input ?? {};
  if (typeof input !== 'object' || Array.isArray(input)) throw new Error('Agent input must be a JSON object');
  const maxSteps = values['max-steps'] ?? input.maxSteps;
  const config = {
    model: requiredString(values.model, 'Agent model (--model)'),
    prompt: requiredString(positionals.length ? positionals.join(' ') : input.prompt, 'Agent prompt'),
    maxSteps: maxSteps == null ? undefined : Number(maxSteps),
    timeoutSeconds: Number(values.timeout ?? input.timeoutSeconds ?? 600),
    options: input.options ?? {},
  };
  if (config.maxSteps !== undefined && (!Number.isSafeInteger(config.maxSteps) || config.maxSteps < 1)) throw new Error('maxSteps must be a positive integer');
  if (!Number.isFinite(config.timeoutSeconds) || config.timeoutSeconds <= 0 || config.timeoutSeconds > 86400) throw new Error('timeoutSeconds must be between 0 and 86400 seconds');
  if (!config.options || typeof config.options !== 'object' || Array.isArray(config.options)) throw new Error('Agent options must be an object');
  const transcript = join(dirname(job.result), 'agent.json');
  const state = { id: job.id };
  const definition = suppliedDefinition ?? agentDefinition(env.ASYS_ENVIRONMENT_DIR, values.agent, env.ASYS_WORKERS_DIR);
  return runAgent({ config, job: state, workspace: job.workspace,
    jobDirectory: job.directory, definition, extensionPaths: (values.extension ?? []).map(path => resolve(definition.workersDirectory, path)),
    signal, provider,
    save: () => writeJSON(transcript, state),
    event: event ?? ((type, data) => console.log(JSON.stringify({ type, time: new Date().toISOString(), ...data }))),
  });
}
