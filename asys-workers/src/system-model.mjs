import { readJSON } from './files.mjs';
import { requiredString } from './values.mjs';

// Host launchers supply only model defaults, never the rest of their config.
export function systemModel(name, override, env = process.env) {
  if (override !== undefined) return requiredString(override, '--model');
  const path = env.ASYS_SYSTEM_MODELS ?? '/etc/asys/system-models.json';
  const models = readJSON(path, {});
  if (!models || typeof models !== 'object' || Array.isArray(models)) throw new Error(`${path}: expected system-model settings`);
  if (models[name] != null) return requiredString(models[name], `${path}: ${name}`);
  throw new Error(`No model configured for system agent '${name}'.\n` +
    `List available models with: asys-inference models\n` +
    `Set the default with: asys system-model set ${name} MODEL\n` +
    'Or pass --model MODEL. Start a new run after changing the default.');
}
