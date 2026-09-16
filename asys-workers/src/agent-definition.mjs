import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { requiredString } from './values.mjs';

export function agentDefinition(environmentDirectory, name) {
  requiredString(environmentDirectory, 'ASYS_ENVIRONMENT_DIR');
  if (typeof name !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(name)) throw new Error('Agent name is required (--agent NAME)');
  const environment = realpathSync(environmentDirectory);
  const directory = realpathSync(join(environment, 'agents', name));
  if (!statSync(directory).isDirectory()) throw new Error(`Agent ${name} must be a directory`);
  const memoryPath = join(directory, 'memory.md');
  const memory = existsSync(memoryPath) ? readFileSync(memoryPath, 'utf8').trim() : '';
  const resources = kind => [join(environment, kind), join(directory, kind)].filter(existsSync);
  return { name, environment, directory, memory,
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
