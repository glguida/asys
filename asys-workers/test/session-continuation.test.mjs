import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { SessionManager } from '@earendil-works/pi-coding-agent';
import { agent } from '../src/agent.mjs';
import { agentDefinition } from '../src/agent-definition.mjs';
import { assistant, model } from './helpers.mjs';

const execute = promisify(execFile);
const final = text => assistant([{ type: 'text', text: JSON.stringify({ final: text, exception: null }) }]);

async function fixture(t) {
  const workspace = await mkdtemp(join(tmpdir(), 'asys-session-continuation-'));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const environment = join(workspace, 'environment');
  await mkdir(join(environment, 'agents', 'test'), { recursive: true });
  const definition = agentDefinition(environment, 'test');
  const f = { workspace, environment, definition, sessionFile: join(workspace, 'implementation', 'session.jsonl'), requests: [], turns: [] };
  f.run = async (prompt, reply = () => final('Done'), options = {}) => {
    const directory = join(workspace, `turn-${f.turns.length + 1}`);
    f.turns.push(directory);
    const provider = {
      async listModels() { return { models: [model] }; },
      async *infer(request, context) {
        const frame = JSON.parse(request.payload);
        f.requests.push(frame);
        const message = await reply(frame, context);
        yield { payload: JSON.stringify({ type: 'done', reason: message.stopReason, message }) };
      },
    };
    return agent({ job: { id: `turn-${f.turns.length}`, directory, result: join(directory, 'result.json'), workspace, input: { prompt } },
      argv: ['--agent', 'test', '--model', model.id], env: {}, signal: options.signal ?? new AbortController().signal },
    { definition, provider, event: options.event ?? (() => {}), sessionFile: f.sessionFile });
  };
  f.snapshot = async index => JSON.parse(await readFile(join(f.turns[index], 'agent.json'), 'utf8'));
  return f;
}

test('the same explicit session retains tool observations and receives new feedback in a fresh process', async t => {
  const f = await fixture(t);
  let calls = 0;
  await f.run('Implement the original request.', () => ++calls === 1
    ? assistant([{ type: 'toolCall', id: 'write-proof', name: 'write', arguments: { path: 'proof.txt', content: 'completed tool work\n' } }], 'toolUse')
    : final('Ready for review.'));
  const before = await f.snapshot(0);
  const script = join(f.workspace, 'continue.mjs');
  await writeFile(script, `
    import assert from 'node:assert/strict';
    import { join } from 'node:path';
    import { agent } from ${JSON.stringify(new URL('../src/agent.mjs', import.meta.url).href)};
    import { agentDefinition } from ${JSON.stringify(new URL('../src/agent-definition.mjs', import.meta.url).href)};
    import { assistant, model } from ${JSON.stringify(new URL('./helpers.mjs', import.meta.url).href)};
    const [workspace, environment, sessionFile] = process.argv.slice(2);
    const directory = join(workspace, 'continued-process');
    const provider = {
      async listModels() { return { models: [model] }; },
      async *infer(request) {
        const frame = JSON.parse(request.payload);
        const messages = frame.context.messages;
        assert.ok(messages.some(m => m.role === 'user' && JSON.stringify(m.content).includes('Implement the original request.')));
        assert.ok(messages.some(m => m.role === 'toolResult' && m.toolCallId === 'write-proof'));
        assert.ok(messages.some(m => m.role === 'assistant' && JSON.stringify(m.content).includes('Ready for review.')));
        assert.match(JSON.stringify(messages.at(-1).content), /Fix the reviewer finding/);
        const message = assistant([{ type: 'text', text: JSON.stringify({ final: 'Fixed with retained context.', exception: null }) }]);
        yield { payload: JSON.stringify({ type: 'done', reason: 'stop', message }) };
      }
    };
    await agent({ job: { id: 'continued', directory, result: join(directory, 'result.json'), workspace,
        input: { prompt: 'Fix the reviewer finding.' } }, argv: ['--agent', 'test', '--model', model.id],
        env: {}, signal: new AbortController().signal },
      { definition: agentDefinition(environment, 'test'), provider, event() {}, sessionFile });
  `);
  await execute(process.execPath, [script, f.workspace, f.environment, f.sessionFile]);
  const after = JSON.parse(await readFile(join(f.workspace, 'continued-process', 'agent.json'), 'utf8'));
  assert.equal(after.agent.session.header.id, before.agent.session.header.id);
  assert.equal(after.agent.sessionFile, f.sessionFile);
  assert.equal(after.agent.sessionStartEntryCount, before.agent.session.entries.length);
  assert.equal(after.agent.sessionStartLeafId, before.agent.session.leafId);
  assert.equal(after.agent.session.entries.filter(e => e.message?.role === 'toolResult').length, 1, 'completed tools must not be replayed');
  assert.deepEqual(after.agent.session.entries.slice(0, before.agent.session.entries.length), before.agent.session.entries);
  assert.equal(await readFile(join(f.workspace, 'proof.txt'), 'utf8'), 'completed tool work\n');
  assert.deepEqual(await f.snapshot(0), before, 'the earlier turn snapshot stays unchanged');
});

test('compaction entries restore the compacted context rather than replaying summarized messages', async t => {
  const f = await fixture(t);
  await f.run('OLD_CONTEXT_MARKER', () => final('Earlier work.'));
  const manager = SessionManager.open(f.sessionFile);
  const kept = manager.appendMessage({ role: 'user', content: [{ type: 'text', text: 'KEEP_CONTEXT_MARKER' }], timestamp: Date.now() });
  manager.appendMessage(final('Kept result.'));
  manager.appendCompaction('COMPACTION_SUMMARY_MARKER: earlier work is complete.', kept, 90000);
  const identity = manager.getSessionId();
  await f.run('Continue from the reviewer feedback.', frame => {
    const context = JSON.stringify(frame.context.messages);
    assert.match(context, /COMPACTION_SUMMARY_MARKER/);
    assert.match(context, /KEEP_CONTEXT_MARKER/);
    assert.doesNotMatch(context, /OLD_CONTEXT_MARKER/);
    return final('Continued after compaction.');
  });
  const saved = await f.snapshot(1);
  assert.equal(saved.agent.session.header.id, identity);
  assert.equal(saved.agent.session.entries.filter(e => e.type === 'compaction').length, 1);
});

test('ordinary agent jobs remain fresh even in the same workspace', async t => {
  const f = await fixture(t);
  f.sessionFile = undefined;
  await f.run('FIRST_ASSIGNMENT_MARKER');
  await f.run('SECOND_ASSIGNMENT_MARKER', frame => {
    assert.doesNotMatch(JSON.stringify(frame.context.messages), /FIRST_ASSIGNMENT_MARKER/);
    return final('Independent job.');
  });
  assert.notEqual((await f.snapshot(0)).agent.session.header.id, (await f.snapshot(1)).agent.session.header.id);
});

test('different explicit goal session files do not select one another by recency', async t => {
  const f = await fixture(t);
  await f.run('FIRST_GOAL_MARKER');
  const first = await f.snapshot(0);
  f.sessionFile = join(f.workspace, 'other-implementation', 'session.jsonl');
  await f.run('SECOND_GOAL_MARKER', frame => {
    assert.doesNotMatch(JSON.stringify(frame.context.messages), /FIRST_GOAL_MARKER/);
    return final('Separate goal.');
  });
  assert.notEqual(first.agent.session.header.id, (await f.snapshot(1)).agent.session.header.id);
});

test('a durable session header exists before the first provider request', async t => {
  const f = await fixture(t);
  await f.run('Start the task.', async () => {
    const header = JSON.parse((await readFile(f.sessionFile, 'utf8')).split('\n')[0]);
    assert.equal(header.type, 'session');
    assert.equal(header.cwd, f.workspace);
    return final('Started.');
  });
});

test('cancellation retains completed tool observations for the next call', async t => {
  const f = await fixture(t);
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(f.run('Do the first part.', (_frame, { signal }) => {
    if (++calls === 1) return assistant([{ type: 'toolCall', id: 'completed-write', name: 'write', arguments: { path: 'cancel-proof.txt', content: 'done' } }], 'toolUse');
    controller.abort(new Error('Cancelled by fixture'));
    signal.throwIfAborted();
  }, { signal: controller.signal }), /Cancelled by fixture/);
  const cancelled = await f.snapshot(0);
  assert.ok(cancelled.agent.session.entries.some(e => e.message?.role === 'toolResult' && e.message.toolCallId === 'completed-write'));
  await f.run('Resume without repeating completed work.', frame => {
    assert.ok(frame.context.messages.some(m => m.role === 'toolResult' && m.toolCallId === 'completed-write'));
    return final('Resumed.');
  });
  assert.equal((await f.snapshot(1)).agent.session.header.id, cancelled.agent.session.header.id);
});

test('invalid persisted history fails without silently dropping or overwriting records', async t => {
  const f = await fixture(t);
  await f.run('Persist a valid session.');
  const valid = await readFile(f.sessionFile, 'utf8');
  for (const suffix of ['{broken record}\n', '{"type":"message"', '{"type":"message","id":"bad","parentId":"missing"}\n']) {
    const corrupt = valid + suffix;
    await writeFile(f.sessionFile, corrupt);
    await assert.rejects(f.run('Do not discard the damaged history.', () => assert.fail('Must fail before inference')), /Invalid session|Incomplete session/);
    assert.equal(await readFile(f.sessionFile, 'utf8'), corrupt);
  }
});

test('a session from another workspace is rejected before inference', async t => {
  const f = await fixture(t);
  await f.run('Persist the original workspace.');
  const valid = await readFile(f.sessionFile, 'utf8');
  const other = await mkdtemp(join(tmpdir(), 'asys-other-workspace-'));
  t.after(() => rm(other, { recursive: true, force: true }));
  const lines = valid.trimEnd().split('\n');
  const header = JSON.parse(lines[0]);
  lines[0] = JSON.stringify({ ...header, cwd: other });
  const changed = lines.join('\n') + '\n';
  await writeFile(f.sessionFile, changed);
  await assert.rejects(f.run('Do not cross workspaces.', () => assert.fail('Must fail before inference')), /Session workspace does not match/);
  assert.equal(await readFile(f.sessionFile, 'utf8'), changed);
});
