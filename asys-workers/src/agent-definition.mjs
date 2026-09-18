import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { requiredString } from './values.mjs';

export function agentDefinition(environmentDirectory, name, workersDirectory = environmentDirectory) {
  return loadDefinition(environmentDirectory, name, workersDirectory, 'agents');
}

export function systemAgentDefinition(environmentDirectory, name) {
  const directory = fileURLToPath(new URL('../../python/asys/system_agents/', import.meta.url));
  return loadDefinition(environmentDirectory, name, directory, '');
}

function loadDefinition(environmentDirectory, name, workersDirectory, agentParent) {
  requiredString(environmentDirectory, 'ASYS_ENVIRONMENT_DIR');
  if (typeof name !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(name)) throw new Error('Agent name is required (--agent NAME)');
  const environment = realpathSync(environmentDirectory);
  workersDirectory = realpathSync(workersDirectory);
  const directory = realpathSync(join(workersDirectory, agentParent, name));
  if (!statSync(directory).isDirectory()) throw new Error(`Agent ${name} must be a directory`);
  const memoryPath = join(directory, 'memory.md');
  const memory = existsSync(memoryPath) ? readFileSync(memoryPath, 'utf8').trim() : '';
  const promptPath = join(directory, 'prompt.md');
  const prompt = existsSync(promptPath) ? readFileSync(promptPath, 'utf8').trim() : '';
  const resources = kind => [...new Set([environment, workersDirectory, directory])].map(root => join(root, kind)).filter(existsSync);
  return { name, environment, workersDirectory, directory, prompt, memory,
    promptHash: createHash('sha256').update(prompt).digest('hex'),
    memoryHash: createHash('sha256').update(memory).digest('hex'),
    skills: resources('skills'), extensions: resources('extensions').flatMap(root =>
      readdirSync(root).sort().filter(name => /\.(?:[cm]?js|ts)$/u.test(name)).map(name => join(root, name))) };
}

export function agentResult(message) {
  const text = message.content.filter(part => part.type === 'text').map(part => part.text).join('');
  let result;
  try { result = JSON.parse(text); }
  catch (cause) { throw new Error('Agent final response must be a JSON object with final and exception', { cause }); }
  if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('Agent final response must be a JSON object');
  requiredString(result.final, 'Agent final report');
  if (!Object.hasOwn(result, 'exception')) throw new Error('Agent final response must include exception (null or a reason)');
  if (result.exception !== null) requiredString(result.exception, 'Agent exception');
  return result;
}
