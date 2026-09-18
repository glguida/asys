import { parseArgs } from 'node:util';
import { appendFileSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { makeDirectory, prepareFile } from '../../asys-runtime/javascript/permissions.mjs';
import { agent } from './agent.mjs';
import { systemAgentDefinition } from './agent-definition.mjs';
import { human } from './human.mjs';
import { writeJSON } from './files.mjs';
import { systemModel } from './system-model.mjs';
import { requiredString } from './values.mjs';

const instructions = Object.fromEntries(['implement', 'verify'].map(phase =>
  [phase, readFileSync(new URL(`./goal/${phase}.md`, import.meta.url), 'utf8').trim()]));
const now = () => new Date().toISOString();

export function verification(result) {
  if (typeof result.verified !== 'boolean' || !Array.isArray(result.criteria) || !result.criteria.length) {
    throw new Error('Verification must include a boolean verified and a nonempty criteria list');
  }
  for (const criterion of result.criteria) {
    if (!criterion || typeof criterion.satisfied !== 'boolean' || !Array.isArray(criterion.evidence) || !criterion.evidence.length) {
      throw new Error('Each verification criterion needs a boolean satisfied and observed evidence');
    }
    requiredString(criterion.requirement, 'Verification requirement');
    for (const item of criterion.evidence) {
      requiredString(item?.source, 'Evidence source');
      requiredString(item?.observation, 'Evidence observation');
    }
  }
  if (result.verified !== result.criteria.every(criterion => criterion.satisfied)) {
    throw new Error('Verification verdict contradicts its criteria');
  }
  return { verified: result.verified, criteria: result.criteria, final: result.final };
}

export function humanRequest(goal, phase, result, attempt) {
  const stage = phase === 'implement' ? 'implementation' : 'verification';
  return {
    title: `Goal ${stage} needs help`,
    prompt: `${result.question || result.exception}\n\n` +
      `Retry starts a fresh ${stage} session on the current workspace with your guidance. ` +
      'Stop ends this goal without marking it achieved.',
    summary: result.final,
    files: result.review_files ?? [],
    context: { 'Original goal': goal, 'Observed blocker': result.exception },
    details: { phase, attempt },
    form: { type: 'object', properties: {
      action: { type: 'string', oneOf: [{ const: 'retry', title: 'Retry with guidance' }, { const: 'stop', title: 'Stop this goal' }] },
      guidance: { type: 'string', title: 'Guidance or information for the agent' },
    }, required: ['action'], additionalProperties: false },
    uischema: { type: 'VerticalLayout', elements: [
      { type: 'Control', scope: '#/properties/action', label: 'Next action', options: { format: 'radio' } },
      { type: 'Control', scope: '#/properties/guidance', options: { multi: true } },
    ] },
  };
}

export async function goal({ job, argv, env, signal }, { executeAgent = agent, askHuman = human, provider,
  event = (type, data) => console.log(JSON.stringify({ type, time: now(), ...data })) } = {}) {
  const { values } = parseArgs({ args: argv, options: {
    model: { type: 'string' }, 'max-attempts': { type: 'string' },
  } });
  const input = job.input;
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Goal input must be a JSON object');
  const original = requiredString(input.goal, 'Goal');
  const maxAttempts = values['max-attempts'] === undefined ? (input.maxAttempts ?? null) : Number(values['max-attempts']);
  if (maxAttempts !== null && (!Number.isSafeInteger(maxAttempts) || maxAttempts < 1)) throw new Error('maxAttempts must be a positive integer');
  const model = systemModel('simple', values.model, env);
  const definition = systemAgentDefinition(env.ASYS_ENVIRONMENT_DIR, 'simple');
  const state = { version: 1, goal: original, agent: 'simple', model, maxAttempts,
    status: 'running', attempt: 0, sessions: [], human: [], startedAt: now() };
  const save = () => writeJSON(join(job.directory, 'goal.json'), { ...state, updatedAt: now() });
  const finish = (status, final, exception = null) => {
    state.status = status;
    state.final = final;
    state.exception = exception;
    state.finishedAt = now();
    save();
    event('goal.finished', { status, attempt: state.attempt, verified: status === 'completed' });
    return { final, exception, verified: status === 'completed', attempts: state.attempt,
      ...(state.verification?.attempt === state.attempt ? { criteria: state.verification.criteria } : {}) };
  };

  async function phase(name) {
    for (let retry = 1; ; retry++) {
      signal.throwIfAborted();
      const relative = `attempts/${state.attempt}/${name}-${retry}`;
      const directory = join(job.directory, relative);
      makeDirectory(directory, { recursive: true, mode: 0o700 });
      const session = { id: randomUUID(), attempt: state.attempt, phase: name, directory: relative,
        status: 'running', startedAt: now() };
      state.sessions.push(session);
      state.phase = name;
      state.status = 'running';
      const context = { goal: original, humanGuidance: state.human.map(({ phase, question, answer }) => ({ phase, question, answer })) };
      if (name === 'implement') {
        context.previousVerification = state.verification ?? null;
        context.lessons = state.sessions.filter(item => item.phase === 'implement' && item.lessons).map(item => item.lessons);
      }
      const assignment = { prompt: `${instructions[name]}\n\nAssignment data:\n${JSON.stringify(context, null, 2)}` };
      writeJSON(join(directory, 'input.json'), assignment);
      const output = join(directory, 'stdout.log');
      prepareFile(output);
      save();
      event('goal.phase_started', { phase: name, attempt: state.attempt, session: session.id });
      const emit = (type, data) => {
        const entry = { type, time: now(), ...data, phase: name, attempt: state.attempt, session: session.id };
        appendFileSync(output, JSON.stringify(entry) + '\n');
        event(type, entry);
      };
      const result = await executeAgent({
        job: { ...job, id: session.id, directory, result: join(directory, 'result.json'), input: assignment },
        argv: ['--agent', 'simple', '--model', model], env, signal,
      }, { definition, ...(provider ? { provider } : {}), event: emit });
      signal.throwIfAborted();
      writeJSON(join(directory, 'result.json'), result);
      session.result = result;
      session.status = result.exception ? 'needs_human' : 'completed';
      session.finishedAt = now();
      try { session.lessons = readFileSync(join(directory, 'lessons.md'), 'utf8'); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
      save();
      event('goal.phase_finished', { phase: name, attempt: state.attempt, status: session.status, session: session.id });
      if (result.exception === null) return result;
      requiredString(result.exception, 'Agent exception');
      const request = humanRequest(original, name, result, state.attempt);
      writeJSON(join(directory, 'human.request.json'), request);
      state.status = 'needs_human';
      save();
      event('goal.human_requested', { phase: name, attempt: state.attempt, reason: result.exception });
      const answer = await askHuman({ job: { ...job, input: request }, argv: [], env, signal },
        { id: session.id, metadata: { goal_phase: name, goal_attempt: state.attempt, goal_session: session.id } });
      signal.throwIfAborted();
      if (!answer || !['retry', 'stop'].includes(answer.action) ||
          (answer.guidance !== undefined && typeof answer.guidance !== 'string')) throw new Error('Invalid Human response');
      writeJSON(join(directory, 'human.result.json'), answer);
      state.human.push({ phase: name, attempt: state.attempt, session: session.id, question: request.prompt, answer });
      save();
      event('goal.human_answered', { phase: name, attempt: state.attempt, action: answer.action });
      if (answer.action === 'stop') return null;
    }
  }

  save();
  try {
    for (let attempt = 1; maxAttempts === null || attempt <= maxAttempts; attempt++) {
      state.attempt = attempt;
      if (!await phase('implement')) return finish('stopped', 'The human stopped the goal during implementation.', 'Stopped by human');
      const result = await phase('verify');
      if (!result) return finish('stopped', 'The human stopped the goal during verification.', 'Stopped by human');
      state.verification = { ...verification(result), attempt };
      save();
      if (state.verification.verified) return finish('completed', result.final);
    }
    return finish('exhausted', state.verification.final, `Goal remains unmet after ${maxAttempts} attempts`);
  } catch (error) {
    const current = state.sessions.at(-1);
    if (current && ['running', 'needs_human'].includes(current.status)) {
      current.status = signal.aborted ? 'cancelled' : 'failed';
      current.error = error.message;
      current.finishedAt = now();
    }
    if (signal.aborted) {
      state.status = 'cancelled';
      state.finishedAt = now();
      save();
      throw error;
    }
    return finish('failed', `Goal execution stopped: ${error.message}`, error.message);
  }
}
