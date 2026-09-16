import { makeDirectory } from '../../asys-runtime/javascript/permissions.mjs';
import { join } from 'node:path';
import { setTimeout } from 'node:timers/promises';
import { readJSON, writeJSON } from '../../asys-runtime/javascript/queue.mjs';
import { digest } from './values.mjs';
import { isDeepStrictEqual } from 'node:util';

// A program can use these filesystem actions without Pi or BPMN. The producer
// advertises available actions in the immutable job request's metadata.
export class Actions {
  constructor(jobDirectory, entries) { this.directory = join(jobDirectory, 'actions'); this.entries = entries; }
  async start(id, action, input, { signal } = {}) {
    if (!this.entries.some(entry => entry.id === action)) throw new Error(`Unknown action ${action}`);
    id = digest(id);
    makeDirectory(join(this.directory, 'requests'), { recursive: true, mode: 0o700 });
    const path = join(this.directory, 'requests', `${id}.json`);
    const request = { id, action, input: input ?? null };
    let existing;
    try { existing = await readJSON(path); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (existing && !isDeepStrictEqual(existing, request)) throw new Error('Action call ID already has different input');
    if (!existing) await writeJSON(path, request);
    return this.wait(id, { signal, accepted: true });
  }
  async wait(id, { signal, accepted = false } = {}) {
    if (!/^[a-f0-9]{64}$/u.test(id)) throw new Error('Invalid action request ID');
    for (;;) {
      signal?.throwIfAborted();
      let state;
      try { state = await readJSON(join(this.directory, 'results', `${id}.json`)); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (state?.status === 'failed') throw new Error(state.error);
      if (state && (state.status === 'completed' || (accepted && state.status === 'running'))) {
        return { id, action: state.request.action, status: state.status, ...(state.status === 'completed' && { result: state.result }) };
      }
      await setTimeout(50, undefined, { signal });
    }
  }
  async list() {
    try { return await readJSON(join(this.directory, 'state.json')); }
    catch (error) { if (error.code !== 'ENOENT') throw error; return { entries: this.entries, running: [], actions: {} }; }
  }
}

export function actionTools(actions) {
  const text = result => ({ content: [{ type: 'text', text: JSON.stringify(result) }], details: result });
  const inputs = actions.entries.filter(entry => entry.inputPaths?.length)
    .map(entry => `${entry.id} reads ${entry.inputPaths.join(', ')}.`).join('\n');
  return [
    { name: 'list_actions', label: 'List available work', description: 'List available actions, active activities, and submitted action requests.',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
      async execute() { return text(await actions.list()); } },
    { name: 'start_action', label: 'Start work', description: 'Select and start an enabled action. The input argument becomes the action\'s message unchanged: a path such as message.part means input must contain a part field. Use the input paths below when constructing the argument. An invalid call returns an error so you can correct it and try again. Returns its request ID; use wait_action for its result. Completion may enable other actions, which must be selected explicitly.' + (inputs ? `\n${inputs}` : ''),
      parameters: { type: 'object', properties: { action: { type: 'string', enum: actions.entries.map(entry => entry.id) }, input: {} }, required: ['action'], additionalProperties: false },
      async execute(callId, args, signal) { return text(await actions.start(callId, args.action, args.input, { signal })); } },
    { name: 'wait_action', label: 'Wait for work', description: 'Wait for a previously started action to complete and return its result.',
      parameters: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'], additionalProperties: false },
      async execute(_callId, args, signal) { return text(await actions.wait(args.id, { signal })); } },
  ];
}
