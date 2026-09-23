import { isDeepStrictEqual, parseArgs } from 'node:util';
import { appendFileSync, readFileSync, realpathSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { makeDirectory, prepareFile } from '../../asys-runtime/javascript/permissions.mjs';
import { agent } from './agent.mjs';
import { agentDefinition, systemAgentDefinition } from './agent-definition.mjs';
import { readJSON, writeJSON } from './files.mjs';
import { requiredString } from './values.mjs';
import { senateConfig } from './senate/config.mjs';

const phases = ['introduce', 'intervene', 'assess', 'decide'];
const instructions = Object.fromEntries(phases.map(phase =>
  [phase, readFileSync(new URL(`./senate/${phase}.md`, import.meta.url), 'utf8').trim()]));
const now = () => new Date().toISOString();

function report(result, phase) {
  if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('Senate agent result must be an object');
  requiredString(result.final, 'Final report');
  if (result.exception !== null) requiredString(result.exception, 'Agent exception');
  if (result.exception === null && phase === 'assess' && typeof result.consensus !== 'boolean') {
    throw new Error('Princeps assessment must include consensus as a boolean');
  }
  return result;
}

export async function senate({ job, argv, env, signal }, { executeAgent = agent, provider,
  event = (type, data) => console.log(JSON.stringify({ type, time: now(), ...data })) } = {}) {
  const { values } = parseArgs({ args: argv, options: { model: { type: 'string' } } });
  const input = job.input;
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Senate input must be a JSON object');
  const topic = requiredString(input.topic, 'Senate topic');
  const config = senateConfig(input.senate, values.model, env);
  const participants = [config.princeps, ...config.senators].map((participant, index) => {
    const base = participant.agent === null ? systemAgentDefinition(env.ASYS_ENVIRONMENT_DIR, 'simple')
      : agentDefinition(env.ASYS_ENVIRONMENT_DIR, participant.agent);
    const prompt = [base.prompt, `Senate participant: ${participant.name}.`, participant.prompt].filter(Boolean).join('\n\n');
    return { ...participant, id: index === 0 ? 'princeps' : `senator-${index}`,
      definition: { ...base, prompt, promptHash: createHash('sha256').update(prompt).digest('hex') } };
  });
  const workspace = realpathSync(job.workspace);
  const checkpoint = join(job.directory, 'senate.json');
  const state = readJSON(checkpoint, null) ?? { version: 1, topic, config, workspace,
    status: 'running', nextPhase: 'introduce', round: 0, nextSenator: 0,
    sessions: [], transcript: [], startedAt: now() };
  if (state.version !== 1 || state.topic !== topic || state.workspace !== workspace || !isDeepStrictEqual(state.config, config)) {
    throw new Error('Senate checkpoint does not match the requested topic, configuration or workspace');
  }
  if (!Array.isArray(state.sessions) || !Array.isArray(state.transcript) || !phases.includes(state.nextPhase) ||
      !Number.isSafeInteger(state.round) || state.round < 0 || state.round > 3 ||
      !Number.isSafeInteger(state.nextSenator) || state.nextSenator < 0 || state.nextSenator >= config.senators.length ||
      (state.nextPhase === 'introduce' ? state.round !== 0 : state.round < 1) ||
      (state.nextPhase === 'decide' && state.round !== 3)) throw new Error('Invalid Senate checkpoint');
  signal.throwIfAborted();
  if (['completed', 'failed'].includes(state.status)) {
    if (!state.result) throw new Error('Terminal Senate checkpoint has no result');
    return state.result;
  }
  const save = () => writeJSON(checkpoint, { ...state, updatedAt: now() });
  const finish = (final, exception, decision = null) => {
    state.status = exception === null ? 'completed' : 'failed';
    state.finishedAt = now();
    state.result = { final, exception, consensus: decision === 'consensus', rounds: state.round, decision };
  };
  const finishedEvent = () => event('senate.finished', { status: state.status,
    rounds: state.result.rounds, consensus: state.result.consensus, decision: state.result.decision });

  async function step() {
    const phase = state.nextPhase, round = state.round;
    const participant = participants[phase === 'intervene' ? state.nextSenator + 1 : 0];
    const previous = state.sessions.filter(item => item.phase === phase && item.round === round && item.participant === participant.name);
    const invalid = previous.filter(item => item.status === 'invalid');
    if (invalid.length > 1) throw new Error(invalid.at(-1).error);
    const correction = invalid.length ? { problem: invalid.at(-1).error, previousResult: invalid.at(-1).result } : null;
    const relative = `phases/${state.sessions.length + 1}-${phase}`;
    const directory = join(job.directory, relative);
    makeDirectory(directory, { recursive: true, mode: 0o700 });
    const session = { id: randomUUID(), phase, round, participant: participant.name,
      model: participant.model, directory: relative, status: 'running', startedAt: now() };
    state.sessions.push(session);
    const context = { topic, phase, round, maxRounds: 3, participant: participant.name,
      princeps: config.princeps.name, senators: config.senators.map(item => item.name),
      transcript: state.transcript, ...(correction ? { correction } : {}) };
    const assignment = { prompt: `${instructions[phase]}\n\n` +
      (correction ? 'Correct your previous report using the existing work. Do not repeat research just to correct the format.\n\n' : '') +
      `Assignment data:\n${JSON.stringify(context, null, 2)}` };
    writeJSON(join(directory, 'input.json'), assignment);
    const output = join(directory, 'stdout.log');
    prepareFile(output);
    save();
    event('senate.phase_started', { phase, round, participant: participant.name, session: session.id });
    const emit = (type, data) => {
      const entry = { ...data, type, time: now(), phase, round, participant: participant.name, session: session.id };
      appendFileSync(output, JSON.stringify(entry) + '\n');
      event(type, entry);
    };
    const result = await executeAgent({ job: { ...job, id: session.id, directory,
      result: join(directory, 'result.json'), input: assignment },
      argv: ['--agent', participant.agent ?? 'simple', '--model', participant.model], env, signal },
    { definition: participant.definition, ...(provider ? { provider } : {}), event: emit,
      sessionFile: join(job.directory, 'participants', participant.id, 'session.jsonl') });
    signal.throwIfAborted();
    writeJSON(join(directory, 'result.json'), result);
    session.result = result;
    session.finishedAt = now();
    try { report(result, phase); }
    catch (error) {
      session.status = 'invalid';
      session.error = error.message;
      save();
      event('senate.phase_finished', { phase, round, participant: participant.name, status: session.status, session: session.id });
      return;
    }
    if (result.exception !== null) {
      session.status = 'failed';
      finish(result.final, result.exception);
    } else {
      session.status = 'completed';
      state.transcript.push({ phase, round, participant: participant.name, final: result.final,
        ...(phase === 'assess' ? { consensus: result.consensus } : {}) });
      // Publish a contribution and the next speaker together. Recovery never
      // repeats a completed speech or omits it from the next participant's view.
      if (phase === 'introduce') { state.nextPhase = 'intervene'; state.round = 1; }
      else if (phase === 'intervene') {
        if (state.nextSenator + 1 < config.senators.length) state.nextSenator++;
        else { state.nextPhase = 'assess'; state.nextSenator = 0; }
      } else if (phase === 'assess') {
        if (result.consensus) finish(result.final, null, 'consensus');
        else if (round === 3) state.nextPhase = 'decide';
        else { state.round++; state.nextPhase = 'intervene'; }
      } else finish(result.final, null, 'princeps');
    }
    save();
    event('senate.phase_finished', { phase, round, participant: participant.name, status: session.status, session: session.id });
  }

  // An interrupted active turn may have saved tool effects in its own session.
  // Continue that participant's conversation while retaining completed speeches.
  for (const session of state.sessions) {
    if (session.status === 'running') { session.status = 'interrupted'; session.finishedAt = now(); }
  }
  state.status = 'running';
  delete state.finishedAt;
  save();
  try {
    while (state.status === 'running') {
      signal.throwIfAborted();
      await step();
    }
    signal.throwIfAborted();
    finishedEvent();
    return state.result;
  } catch (error) {
    const current = state.sessions.at(-1);
    if (current?.status === 'running') {
      current.status = signal.aborted ? 'cancelled' : 'failed';
      current.error = error.message;
      current.finishedAt = now();
    }
    if (signal.aborted) {
      // A terminal result already committed at a phase boundary stays terminal.
      if (!state.result) { state.status = 'cancelled'; state.finishedAt = now(); }
      save();
      throw error;
    }
    finish(`Senate execution stopped: ${error.message}`, error.message);
    save();
    finishedEvent();
    return state.result;
  }
}
