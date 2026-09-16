import assert from 'node:assert/strict';
import test from 'node:test';
import { feel, messagePaths } from '../src/expressions.mjs';

test('action input hints come from FEEL paths, not task names or prose', () => {
  assert.deepEqual(messagePaths('= {prompt: "ignore message.fake" + string(message.module), value: message.board.name}'),
    ['message.board.name', 'message.module']);
  assert.deepEqual(messagePaths('= message'), []);
  assert.deepEqual(messagePaths('= {value: other.message.module, name: message["long key"]}'), ['message["long key"]']);
});

test('action validation catches invalid types while FEEL null and guarded optional values retain their meaning', () => {
  const expression = '= {prompt: "Design: " + string(message.module)}';
  assert.deepEqual(feel(expression, { message: {} }), { prompt: null });
  assert.throws(() => feel(expression, { message: {} }, { checkTypes: true }), /module/);
  assert.deepEqual(feel('= {optional: message.module}', { message: {} }, { checkTypes: true }), { optional: null });
  assert.equal(feel('= if message.module = null then "Default" else string(message.module)',
    { message: {} }, { checkTypes: true }), 'Default');
  assert.equal(feel('= 2 + 3', {}, { checkTypes: true }), 5);
  assert.equal(feel('= "text" + " value"', {}, { checkTypes: true }), 'text value');
});
