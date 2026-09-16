import assert from 'node:assert/strict';
import test from 'node:test';
import { actionTools } from '../src/actions.mjs';

test('the coordinator sees which action input fields its workflow consumes', () => {
  const actions = { entries: [
    { id: 'design_module', inputPaths: ['message.module'] },
    { id: 'review', inputPaths: ['message.design', 'message.criteria'] },
  ] };
  const start = actionTools(actions).find(tool => tool.name === 'start_action');
  assert.match(start.description, /input.*message.*unchanged/s);
  assert.match(start.description, /design_module.*message\.module/);
  assert.match(start.description, /review.*message\.design.*message\.criteria/);
});
