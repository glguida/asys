import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { goal } from '../src/goal.mjs';
import { systemModel } from '../src/system-model.mjs';
import { assistant, model } from './helpers.mjs';

const implemented = { final: 'IMPLEMENTER_CLAIMS_SUCCESS', exception: null };
const criterion = { id: 'C1', requirement: 'Produce the required output', basis: 'Original user request',
  verification: 'Inspect output.txt and check that it contains the required output.' };
const defined = { final: 'The request requires the output file.', exception: null, contract: { criteria: [criterion] } };
const reviewed = { final: 'Compared the criterion and check with the original request; coverage is sufficient.', exception: null, decision: 'accept' };
const blocked = { final: 'Inspected the inputs. The required specification is missing; existing work is preserved.',
  exception: 'Missing specification', question: 'Please supply the expected output format.' };
const assignment = context => JSON.parse(context.job.input.prompt.split('Assignment data:\n')[1]);
const checked = (data, satisfied = true) => ({ final: satisfied ? 'Observed the required output.' : 'Output is still missing.',
  exception: null, coverage: 'complete',
  criteria: data.contract.criteria.map(item => ({ id: item.id, status: satisfied ? 'satisfied' : 'unmet',
    evidence: [{ source: 'cat output.txt', observation: satisfied ? 'The file contains the required output.' : 'File does not exist.' }] })),
  resolved_findings: satisfied ? data.unresolvedFindings.map(item =>
    ({ id: item.id, reason: 'Read the current output and checked the previously missing behavior.' })) : [] });
const normal = data => ({ define: defined, review: reviewed, implement: implemented })[data.phase] ?? checked(data);

async function fixture(t, input = {}) {
  const root = await mkdtemp(join(tmpdir(), 'asys-goal-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const directory = join(root, 'job'), workspace = join(root, 'workspace'), environment = join(root, 'environment');
  for (const path of [directory, workspace, environment]) await mkdir(path);
  await mkdir(join(environment, 'agents/simple'), { recursive: true });
  await writeFile(join(environment, 'agents/simple/prompt.md'), 'ENVIRONMENT_PRIVATE_PROMPT');
  await writeFile(join(environment, 'agents/simple/memory.md'), 'ENVIRONMENT_PRIVATE_MEMORY');
  const models = join(root, 'models.json');
  await writeFile(models, JSON.stringify({ simple: model.id }));
  const controller = new AbortController(), events = [];
  const context = { job: { id: 'goal-test', directory, workspace, result: join(directory, 'result.json'),
    input: { goal: 'Produce the required output', ...input } }, argv: [],
    env: { ASYS_ENVIRONMENT_DIR: environment, ASYS_SYSTEM_MODELS: models }, signal: controller.signal };
  return { root, directory, workspace, models, controller, context, events,
    run(dependencies = {}) { return goal(context, { event: (type, data) => events.push({ type, ...data }), ...dependencies }); },
    state: () => readFile(join(directory, 'goal.json'), 'utf8').then(JSON.parse),
  };
}

test('goal reviews criteria then repeats fresh simple sessions, preserving findings and lessons', async t => {
  const f = await fixture(t), calls = [];
  let implementations = 0, verifications = 0;
  const result = await f.run({ async executeAgent(context, dependencies) {
    calls.push(context);
    const data = assignment(context);
    assert.match(dependencies.definition.prompt, /simple system agent supplied by asys/);
    assert.equal(dependencies.definition.memory, '');
    assert.deepEqual(context.argv, ['--agent', 'simple', '--model', model.id]);
    if (data.phase === 'implement' && ++implementations === 1) {
      await writeFile(join(context.job.directory, 'lessons.md'), 'CHECK_THE_OUTPUT_FILE');
    }
    return data.phase === 'verify' ? checked(data, ++verifications > 1) : normal(data);
  } });
  assert.equal(result.verified, true);
  assert.equal(result.attempts, 2);
  assert.deepEqual(calls.map(call => assignment(call).phase), ['define', 'review', 'implement', 'verify', 'implement', 'verify']);
  assert.equal(new Set(calls.map(call => call.job.id)).size, 6);
  assert.equal(new Set(calls.map(call => call.job.directory)).size, 6);
  assert.equal(new Set(calls.map(call => call.job.workspace)).size, 1);
  assert.match(calls[4].job.input.prompt, /Output is still missing/);
  assert.match(calls[4].job.input.prompt, /CHECK_THE_OUTPUT_FILE/);
  for (const call of [calls[3], calls[5]]) {
    assert.doesNotMatch(call.job.input.prompt, /IMPLEMENTER_CLAIMS_SUCCESS|CHECK_THE_OUTPUT_FILE/);
    assert.match(call.job.input.prompt, /not proof/);
    assert.equal(assignment(call).contract.criteria[0].id, 'C1');
  }
  assert.equal(assignment(calls[5]).unresolvedFindings[0].id, 'F1');
  const state = await f.state();
  assert.equal(state.goal, f.context.job.input.goal);
  assert.equal(state.status, 'completed');
  assert.equal(state.sessions[2].lessons, 'CHECK_THE_OUTPUT_FILE');
  assert.equal(state.contracts[0].review.decision, 'accept');
  assert.equal(state.findings[0].status, 'resolved');
  for (const session of state.sessions) {
    assert.deepEqual(JSON.parse(await readFile(join(f.directory, session.directory, 'result.json'))), session.result);
  }
});

test('a rejected contract is revised and reviewed before any implementation', async t => {
  const f = await fixture(t), phases = [];
  let reviews = 0;
  const result = await f.run({ async executeAgent(context) {
    const data = assignment(context);
    phases.push(data.phase);
    if (data.phase === 'review' && ++reviews === 1) return { ...reviewed, decision: 'revise', final: 'Specify the output encoding.' };
    if (data.phase === 'define' && reviews) {
      assert.equal(data.reviewHistory[0].feedback, 'Specify the output encoding.');
      return { ...defined, contract: { criteria: [{ ...criterion, verification: 'Read output.txt as UTF-8 and check its contents.' }] } };
    }
    if (data.phase === 'implement') assert.equal(data.contract.revision, 2);
    return normal(data);
  } });
  assert.equal(result.verified, true);
  assert.deepEqual(phases, ['define', 'review', 'define', 'review', 'implement', 'verify']);
  const state = await f.state();
  assert.equal(state.contracts.length, 2);
  assert.equal(new Set(state.sessions.map(item => item.directory)).size, 6);
  assert.deepEqual(state.sessions.slice(0, 4).map(item => item.attempt), [0, 0, 0, 0]);
});

test('attempt exhaustion is unsuccessful and preserves observed gaps', async t => {
  const f = await fixture(t, { maxAttempts: 2 });
  let calls = 0;
  const result = await f.run({ async executeAgent(context) {
    calls++;
    const data = assignment(context);
    return data.phase === 'verify' ? checked(data, false) : normal(data);
  } });
  assert.equal(calls, 6);
  assert.equal(result.verified, false);
  assert.match(result.exception, /after 2 attempts/);
  assert.equal(result.criteria[0].status, 'unmet');
  assert.equal(result.findings.length, 1, 'identical repeated observations do not duplicate the finding');
  assert.equal((await f.state()).status, 'exhausted');
});

test('the default keeps working beyond three attempts until verification succeeds', async t => {
  const f = await fixture(t);
  let verifications = 0;
  const result = await f.run({ async executeAgent(context) {
    const data = assignment(context);
    return data.phase === 'verify' ? checked(data, ++verifications === 4) : normal(data);
  } });
  assert.equal(result.verified, true);
  assert.equal(result.attempts, 4);
  assert.equal((await f.state()).maxAttempts, null);
});

test('a passing checklist cannot erase an unresolved finding by omission', async t => {
  const f = await fixture(t), observed = [];
  let verifications = 0;
  const result = await f.run({ async executeAgent(context) {
    const data = assignment(context);
    if (data.phase !== 'verify') return normal(data);
    observed.push(data.unresolvedFindings);
    if (++verifications === 1) return checked(data, false);
    if (verifications === 2) return { ...checked(data), verified: true, resolved_findings: [] };
    return checked(data);
  } });
  assert.equal(result.verified, true);
  assert.equal(result.attempts, 3);
  assert.equal(observed[2][0].id, 'F1');
  assert.equal((await f.state()).findings[0].resolvedAttempt, 3);
});

for (const origin of ['implement', 'verify']) {
  test(origin + ' can reopen the contract without losing existing findings', async t => {
    const f = await fixture(t), phases = [];
    let amended = false;
    const result = await f.run({ async executeAgent(context) {
      const data = assignment(context);
      phases.push(data.phase);
      if (data.phase === origin && !amended) {
        amended = true;
        return { ...(origin === 'implement' ? implemented : checked(data, false)),
          coverage: 'gap', contract_changes: 'The request also requires a UTF-8 output; add that missing check.' };
      }
      if (data.phase === 'define' && amended) {
        assert.match(data.contractChanges, /UTF-8/);
        if (origin === 'verify') assert.equal(data.unresolvedFindings[0].id, 'F1');
        return { ...defined, contract: { criteria: [criterion, { ...criterion, id: 'C2', requirement: 'Output uses UTF-8' }] } };
      }
      if (data.phase === 'verify') assert.equal(data.contract.criteria.length, 2);
      return normal(data);
    } });
    assert.equal(result.verified, true);
    assert.equal(result.attempts, 2);
    assert.equal((await f.state()).contract.revision, 2);
    assert.equal(phases.filter(phase => phase === 'review').length, 2);
    if (origin === 'verify') assert.equal((await f.state()).findings[0].status, 'resolved');
  });
}

test('an unverified observation stays distinct from an implementation failure', async t => {
  const f = await fixture(t, { maxAttempts: 1 });
  const result = await f.run({ async executeAgent(context) {
    const data = assignment(context);
    return data.phase === 'verify' ? { ...checked(data), criteria: [{ id: 'C1', status: 'unverified',
      explanation: 'The check could not obtain a result.', evidence: [] }] } : normal(data);
  } });
  assert.equal(result.verified, false);
  assert.equal(result.criteria[0].status, 'unverified');
  assert.equal(result.findings[0].explanation, 'The check could not obtain a result.');
});

for (const phase of ['define', 'review', 'implement', 'verify']) {
  test(phase + ' asks a human, waits, and retries in a fresh session', async t => {
    const f = await fixture(t), calls = [];
    let answer, asked, blockedOnce = false;
    const attention = new Promise(resolve => { asked = resolve; });
    const pending = f.run({ async executeAgent(context) {
      calls.push(context);
      const data = assignment(context);
      if (data.phase === phase && !blockedOnce) { blockedOnce = true; return blocked; }
      return normal(data);
    }, askHuman(context) {
      assert.equal(context.signal, f.controller.signal);
      assert.match(context.job.input.prompt, /expected output format/);
      assert.match(context.job.input.prompt, /Retry starts a fresh/);
      assert.equal(context.job.input.summary, blocked.final);
      assert.equal(context.job.input.context['Original goal'], f.context.job.input.goal);
      asked();
      return new Promise(resolve => { answer = resolve; });
    } });
    await attention;
    assert.equal((await f.state()).status, 'needs_human');
    answer({ action: 'retry', guidance: 'Use the supplied specification.' });
    const result = await pending;
    assert.equal(result.verified, true);
    assert.equal(result.attempts, 1);
    const retried = calls.filter(call => assignment(call).phase === phase);
    assert.equal(retried.length, 2);
    const data = assignment(retried[1]);
    assert.equal(data.humanGuidance[0].answer.guidance, 'Use the supplied specification.');
    assert.equal((await f.state()).human[0].phase, phase);
    assert.equal(new Set(calls.map(call => call.job.id)).size, 5);
  });
}

test('a human stop during definition never becomes a successful verification', async t => {
  const f = await fixture(t);
  const result = await f.run({ async executeAgent() { return blocked; },
    async askHuman() { return { action: 'stop', guidance: 'The requested hardware is unavailable.' }; } });
  assert.equal(result.verified, false);
  assert.equal(result.exception, 'Stopped by human');
  assert.equal(result.attempts, 0);
  assert.equal((await f.state()).status, 'stopped');
});

for (const waiting of ['agent', 'human']) {
  test('cancellation interrupts an active ' + waiting + ' without starting another phase', async t => {
    const f = await fixture(t);
    let ready, calls = 0;
    const started = new Promise(resolve => { ready = resolve; });
    const wait = ({ signal }) => new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      ready();
    });
    const pending = f.run({ executeAgent(context) { calls++; return waiting === 'agent' ? wait(context) : blocked; }, askHuman: wait });
    const rejected = assert.rejects(pending, /Cancelled by test/);
    await started;
    f.controller.abort(new Error('Cancelled by test'));
    await rejected;
    assert.equal(calls, 1);
    assert.equal((await f.state()).status, 'cancelled');
  });
}

test('malformed reports receive one correction opportunity without another implementation', async t => {
  const f = await fixture(t);
  let verifications = 0, implementations = 0;
  const result = await f.run({ async executeAgent(context) {
    const data = assignment(context);
    if (data.phase === 'implement') implementations++;
    if (data.phase === 'verify' && ++verifications === 1) return { ...checked(data), criteria: [] };
    if (data.phase === 'verify') assert.match(data.correction.problem, /omitted criteria: C1/);
    return normal(data);
  } });
  assert.equal(result.verified, true);
  assert.equal(implementations, 1);
  assert.equal(verifications, 2);
  assert.equal((await f.state()).sessions[3].status, 'invalid');
});

test('missing, duplicate, unknown and unsupported criteria never claim completion', async t => {
  const invalid = [
    data => ({ ...checked(data), criteria: [] }),
    data => ({ ...checked(data), coverage: 'maybe' }),
    data => ({ ...checked(data), criteria: [checked(data).criteria[0], checked(data).criteria[0]] }),
    data => ({ ...checked(data), criteria: [{ ...checked(data).criteria[0], id: 'UNKNOWN' }] }),
    data => ({ ...checked(data), criteria: [{ ...checked(data).criteria[0], evidence: [] }] }),
    data => ({ ...checked(data), resolved_findings: [{ id: 'F99', reason: 'Claimed fixed' }] }),
  ];
  for (const verdict of invalid) {
    const f = await fixture(t);
    let verifications = 0;
    const result = await f.run({ async executeAgent(context) {
      const data = assignment(context);
      if (data.phase === 'verify') { verifications++; return verdict(data); }
      return normal(data);
    } });
    assert.equal(result.verified, false);
    assert.ok(result.exception);
    assert.equal(verifications, 2);
    assert.equal((await f.state()).status, 'failed');
  }
});

test('a positive worker boolean cannot override an unmet criterion', async t => {
  const f = await fixture(t, { maxAttempts: 1 });
  const result = await f.run({ async executeAgent(context) {
    const data = assignment(context);
    return data.phase === 'verify' ? { ...checked(data, false), verified: true } : normal(data);
  } });
  assert.equal(result.verified, false);
  assert.equal((await f.state()).status, 'exhausted');
});

test('cancellation at verification handoff wins over a positive assessment', async t => {
  const f = await fixture(t);
  await assert.rejects(f.run({ async executeAgent(context) { return normal(assignment(context)); },
    event(type, data) {
      if (type === 'goal.phase_finished' && data.phase === 'verify') f.controller.abort(new Error('Cancelled at handoff'));
    },
  }), /Cancelled at handoff/);
  assert.equal((await f.state()).status, 'cancelled');
});

test('model defaults and overrides are resolved once, and invalid limits fail before inference', async t => {
  const f = await fixture(t);
  assert.equal(systemModel('simple', undefined, f.context.env), model.id);
  await writeFile(f.models, '{}');
  assert.throws(() => systemModel('simple', undefined, f.context.env), /asys system-model set simple MODEL/);
  await writeFile(f.models, 'broken');
  f.context.argv = ['--model', model.id, '--max-attempts', '1'];
  assert.equal((await f.run({ async executeAgent(context) { return normal(assignment(context)); } })).verified, true);
  for (const maxAttempts of [0, -1, true, '2', 1.5]) {
    const invalid = await fixture(t, { maxAttempts });
    await assert.rejects(invalid.run({ executeAgent() { assert.fail('must not start'); } }), /positive integer/);
  }
});

test('real simple sessions use workspace tools with independent observable transcripts', async t => {
  const f = await fixture(t), initial = [], requests = [];
  const provider = {
    async listModels() { return { models: [model] }; },
    async *infer(request) {
      assert.equal(request.model, model.id);
      const frame = JSON.parse(request.payload);
      requests.push(frame);
      const prompt = frame.context.messages[0].content[0].text;
      const data = JSON.parse(prompt.split('Assignment data:\n')[1]);
      const fresh = frame.context.messages.length === 1;
      if (fresh) initial.push(frame);
      assert.match(frame.context.systemPrompt, /simple system agent supplied by asys/);
      assert.doesNotMatch(frame.context.systemPrompt, /ENVIRONMENT_PRIVATE/);
      const command = { implement: 'printf required > output.txt', verify: 'cat output.txt' }[data.phase] ?? 'pwd';
      const message = fresh
        ? assistant([{ type: 'toolCall', id: 'check-' + requests.length, name: 'bash', arguments: { command } }], 'toolUse')
        : assistant([{ type: 'text', text: JSON.stringify(normal(data)) }]);
      yield { payload: JSON.stringify({ type: 'done', reason: message.stopReason, message }) };
    },
  };
  const result = await f.run({ provider });
  assert.equal(result.verified, true);
  assert.equal(initial.length, 4);
  assert.equal(await readFile(join(f.workspace, 'output.txt'), 'utf8'), 'required');
  assert.doesNotMatch(initial[3].context.messages[0].content[0].text, /IMPLEMENTER_CLAIMS_SUCCESS/);
  const state = await f.state();
  for (const session of state.sessions) {
    const transcript = JSON.parse(await readFile(join(f.directory, session.directory, 'agent.json')));
    assert.equal(transcript.agent.name, 'simple');
    assert.equal(transcript.agent.steps, 2);
  }
  const script = [
    'import json, sys',
    'from pathlib import Path',
    'sys.path.insert(0, sys.argv[1])',
    'from asys.transcript import JobOutput',
    'title, lines = JobOutput().read({"directory": sys.argv[2]})',
    'print(json.dumps({"title": title, "text": "\\n".join(lines)}))',
  ].join('\n');
  const { stdout } = await promisify(execFile)('python3', ['-c', script,
    fileURLToPath(new URL('../../python', import.meta.url)), f.directory]);
  const observed = JSON.parse(stdout);
  assert.equal(observed.title, 'Goal');
  assert.match(observed.text, /Attempt 0: define/);
  assert.match(observed.text, /Attempt 0: review/);
  assert.match(observed.text, /Attempt 1: implement/);
  assert.match(observed.text, /Attempt 1: verify/);
  assert.match(observed.text, /Tool call: bash/);
});
