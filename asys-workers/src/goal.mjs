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
import { contract, review, verification, reconcileFindings } from './goal/results.mjs';

const instructions = Object.fromEntries(['define', 'review', 'implement', 'verify'].map(phase =>
  [phase, readFileSync(new URL(`./goal/${phase}.md`, import.meta.url), 'utf8').trim()]));
const shared = readFileSync(new URL('./goal/shared.md', import.meta.url), 'utf8').trim();
const now = () => new Date().toISOString();
const stages = { define: 'definition', review: 'contract review', implement: 'implementation', verify: 'verification' };

export function humanRequest(goal, phase, result, attempt) {
  const stage = stages[phase];
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
    status: 'running', attempt: 0, sessions: [], human: [], contracts: [], contract: null,
    findings: [], contractChanges: null, startedAt: now() };
  const save = () => writeJSON(join(job.directory, 'goal.json'), { ...state, updatedAt: now() });
  const finish = (status, final, exception = null) => {
    state.status = status;
    state.final = final;
    state.exception = exception;
    state.finishedAt = now();
    save();
    event('goal.finished', { status, attempt: state.attempt, verified: status === 'completed' });
    return { final, exception, verified: status === 'completed', attempts: state.attempt,
      ...(state.verification?.attempt === state.attempt && state.verification.contractRevision === state.contract?.revision
        ? { criteria: state.verification.criteria } : {}),
      findings: state.findings.filter(item => item.status === 'open') };
  };

  function assignmentData(name) {
    const context = { phase: name, goal: original, contract: state.contract,
      unresolvedFindings: state.findings.filter(item => item.status === 'open'),
      humanGuidance: state.human.map(({ phase, question, answer }) => ({ phase, question, answer })) };
    if (name === 'define' || name === 'review') {
      context.proposal = state.proposal ?? null;
      context.contractChanges = state.contractChanges;
      context.reviewHistory = state.contracts.filter(item => item.review).map(item =>
        ({ revision: item.revision, decision: item.review.decision, feedback: item.review.final }));
    }
    if (name === 'implement') {
      context.previousVerification = state.verification ?? null;
      context.previousImplementation = state.implementation ?? null;
      context.lessons = state.sessions.filter(item => item.phase === 'implement' && item.lessons).map(item => item.lessons);
    }
    return context;
  }

  async function phase(name, validate = result => result) {
    let correction = null;
    for (;;) {
      signal.throwIfAborted();
      const retry = state.sessions.filter(item => item.attempt === state.attempt && item.phase === name).length + 1;
      const relative = `attempts/${state.attempt}/${name}-${retry}`;
      const directory = join(job.directory, relative);
      makeDirectory(directory, { recursive: true, mode: 0o700 });
      const session = { id: randomUUID(), attempt: state.attempt, phase: name, directory: relative,
        status: 'running', startedAt: now() };
      state.sessions.push(session);
      state.phase = name;
      state.status = 'running';
      const context = { ...assignmentData(name), ...(correction ? { correction } : {}) };
      const assignment = { prompt: `${instructions[name]}\n\n${shared}\n\nAssignment data:\n${JSON.stringify(context, null, 2)}` };
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
      let validated, invalid;
      if (result.exception === null) {
        try {
          requiredString(result.final, 'Final report');
          if (result.contract_changes != null && result.contract_changes !== '') {
            requiredString(result.contract_changes, 'Proposed contract changes');
          }
          validated = validate(result);
        }
        catch (error) { invalid = error; session.status = 'invalid'; session.error = error.message; }
      }
      save();
      event('goal.phase_finished', { phase: name, attempt: state.attempt, status: session.status, session: session.id });
      signal.throwIfAborted();
      if (invalid) {
        if (correction) throw invalid;
        correction = { problem: invalid.message, previousResult: result };
        continue;
      }
      if (result.exception === null) return validated;
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
      correction = null;
    }
  }

  async function agreeContract() {
    for (;;) {
      const proposal = await phase('define', contract);
      if (!proposal) return false;
      const entry = { revision: state.contracts.length + 1, contract: proposal };
      state.proposal = proposal;
      state.contracts.push(entry);
      save();
      const result = await phase('review', review);
      if (!result) return false;
      entry.review = result;
      if (result.decision === 'accept') {
        state.contract = { ...proposal, revision: entry.revision };
        state.contractChanges = null;
        save();
        return true;
      }
      save();
    }
  }

  save();
  try {
    if (!await agreeContract()) return finish('stopped', 'The human stopped the goal while defining success.', 'Stopped by human');
    for (let attempt = 1; maxAttempts === null || attempt <= maxAttempts; attempt++) {
      if (state.contractChanges && !await agreeContract()) {
        return finish('stopped', 'The human stopped the goal while revising success criteria.', 'Stopped by human');
      }
      state.attempt = attempt;
      const implementation = await phase('implement');
      if (!implementation) return finish('stopped', 'The human stopped the goal during implementation.', 'Stopped by human');
      state.implementation = implementation;
      if (implementation.contract_changes) {
        state.contractChanges = implementation.contract_changes;
        save();
        continue;
      }
      const result = await phase('verify', result => verification(result, state.contract, state.findings));
      if (!result) return finish('stopped', 'The human stopped the goal during verification.', 'Stopped by human');
      state.verification = { ...result, attempt, contractRevision: state.contract.revision };
      reconcileFindings(state.findings, result, attempt);
      state.contractChanges = result.contract_changes || (result.coverage !== 'complete' ? result.final : null);
      save();
      if (result.verified) return finish('completed', result.final);
    }
    return finish('exhausted', state.implementation?.contract_changes || state.verification?.final || state.implementation.final,
      `Goal remains unmet after ${maxAttempts} attempts`);
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
