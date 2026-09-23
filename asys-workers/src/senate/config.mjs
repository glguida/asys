import { systemModel } from '../system-model.mjs';
import { requiredString } from '../values.mjs';

function object(value, label, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be a JSON object`);
  for (const key of Object.keys(value)) {
    if (!keys.includes(key)) throw new Error(`Unknown ${label} field: ${key}`);
  }
}

function text(value, label) {
  requiredString(value, label);
  if (value.includes('\0')) throw new Error(`${label} must not contain NUL`);
  return value;
}

// Models remain Provider names. Connection settings and credentials belong to
// inference components, never to a participant definition.
export function senateConfig(value, override, env) {
  object(value, 'Senate configuration', ['version', 'princeps', 'senators']);
  if (value.version !== 1) throw new Error('Senate configuration version must be 1');
  if (!Array.isArray(value.senators) || !value.senators.length) throw new Error('Senate requires at least one senator');
  if (override !== undefined) text(override, '--model');
  const names = new Set();
  const participant = (entry, label) => {
    object(entry, label, ['name', 'prompt', 'agent', 'model']);
    const name = text(entry.name, `${label} name`);
    if (names.has(name.trim())) throw new Error('Senate participant names must be unique');
    names.add(name.trim());
    if (entry.prompt !== undefined && (typeof entry.prompt !== 'string' || entry.prompt.includes('\0'))) {
      throw new Error(`${label} prompt must be text without NUL`);
    }
    if (entry.agent !== undefined && (typeof entry.agent !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(entry.agent))) {
      throw new Error(`${label} agent must be a valid environment agent name`);
    }
    const model = entry.model === undefined ? systemModel('simple', override, env) : text(entry.model, `${label} model`);
    return { name, agent: entry.agent ?? null, prompt: entry.prompt ?? '', model };
  };
  return { version: 1, princeps: participant(value.princeps, 'Princeps'),
    senators: value.senators.map((entry, index) => participant(entry, `Senator ${index + 1}`)) };
}
