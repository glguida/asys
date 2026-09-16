import { makeDirectory } from '../../asys-runtime/javascript/permissions.mjs';
import { dirname, join } from 'node:path';
import { setTimeout } from 'node:timers/promises';
import { isDeepStrictEqual } from 'node:util';
import Ajv from 'ajv';
import { readJSON, writeJSON } from './files.mjs';
import { requiredString } from './values.mjs';
import { notifyHuman } from './human-attention.mjs';

export function humanInput(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Human-task input must be an object');
  requiredString(input.prompt, 'Human-task prompt');
  if (input.title !== undefined && typeof input.title !== 'string') throw new Error('Human-task title must be text');
  if (input.candidates !== undefined && (!Array.isArray(input.candidates) || input.candidates.some(v => typeof v !== 'string' || !v))) throw new Error('Candidates must be a list of names');
  if (input.form !== undefined) new Ajv({ strict: true }).compile(input.form);
  return input;
}

export async function human({ job, argv, env, signal }) {
  if (argv.length) throw new Error('asys-human takes its task description from the job input');
  const input = humanInput(job.input);
  const directory = join(job.directory, 'human');
  makeDirectory(directory, { recursive: true, mode: 0o700 });
  const path = join(directory, 'request.json');
  const request = { version: 1, id: job.id, input };
  const existing = readJSON(path, null);
  if (existing && !isDeepStrictEqual(existing, request)) throw new Error('Saved human task uses different input');
  if (!existing) writeJSON(path, request);
  notifyHuman(dirname(dirname(dirname(env.ASYS_REQUEST))));
  for (;;) {
    signal.throwIfAborted();
    const state = readJSON(join(directory, 'state.json'), null);
    if (state?.status === 'completed') return state.result;
    await setTimeout(100, undefined, { signal });
  }
}
