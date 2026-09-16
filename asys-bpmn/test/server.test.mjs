import assert from 'node:assert/strict';
import { once } from 'node:events';
import test from 'node:test';
import { Workflow } from '../gen/asys/workflow/v1/workflow_pb.js';
import { workflowServer } from '../src/server.mjs';

test('the component API dispatches Connect requests to the runtime', async t => {
  let calls = 0;
  const runtime = Object.fromEntries(Workflow.methods.map(method => [method.localName, () => ({})]));
  runtime.listWorkflows = () => { calls++; return { workflows: [{ id: 'example', name: 'Example' }] }; };
  const server = workflowServer(runtime);
  t.after(() => { server.closeConnections(); server.close(); });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const response = await fetch(`http://127.0.0.1:${server.address().port}/asys.workflow.v1.Workflow/ListWorkflows`, {
    method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json', 'Connect-Protocol-Version': '1' },
  });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).workflows[0].id, 'example');
  assert.equal(calls, 1);
});
