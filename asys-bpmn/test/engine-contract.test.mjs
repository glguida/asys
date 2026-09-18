import test from 'node:test';
import assert from 'node:assert/strict';
import { setImmediate } from 'node:timers/promises';
import BpmnModdle from 'bpmn-moddle';
import serializer, { deserialize, TypeResolver } from 'moddle-context-serializer';
import * as Elements from 'bpmn-elements';
import { workflow, flow } from './helpers.mjs';

// Exercise the dependencies directly: no asys adapter, runtime, workers, or Docker.
async function serialized(body) {
  return JSON.parse(serializer(await new BpmnModdle().fromXML(workflow(body)), TypeResolver(Elements)).serialize());
}

async function engine(t, body, { extend, environment = {}, definition } = {}) {
  const context = deserialize(definition ?? await serialized(body), TypeResolver(Elements, extend));
  const instance = new Elements.Definition(Elements.Context(context, new Elements.Environment(environment)));
  t.after(() => instance.stop());
  return instance;
}

const multi = '<bpmn:multiInstanceLoopCharacteristics><bpmn:loopCardinality>1</bpmn:loopCardinality></bpmn:multiInstanceLoopCharacteristics>';

for (const type of ['task', 'subProcess']) {
  test(`P1: ${type} constructs the registered loop and executes its root scope`, async t => {
    // src/loop.mjs relies on constructor dispatch, rather than replacing an instance method.
    const calls = [];
    class Loop extends Elements.MultiInstanceLoopCharacteristics {
      constructor(activity, definition) {
        super(activity, definition);
        calls.push({ activity, definition });
      }
      execute(message) {
        calls[0].message = message;
        return super.execute(message);
      }
    }
    const definition = await engine(t, `<bpmn:${type} id="work">${multi}${type === 'subProcess' ? '<bpmn:task id="child"/>' : ''}</bpmn:${type}>`,
      { extend: mapper => { mapper['bpmn:MultiInstanceLoopCharacteristics'] = Loop; } });
    definition.run();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].activity.id, 'work');
    assert.equal(calls[0].definition.type, 'bpmn:MultiInstanceLoopCharacteristics');
    assert.equal(calls[0].message.content.isRootScope, true);
    assert.equal(definition.isRunning, false);
    assert.equal(definition.counters.completed, 1);
  });
}

test('P2: native testBefore loop hangs when its initial stop condition is true', async t => {
  // src/loop.mjs works around this defect. If this pin fails after an upgrade,
  // remove the empty-loop workaround instead of preserving the upstream bug.
  const definition = await engine(t, `<bpmn:task id="work"><bpmn:standardLoopCharacteristics testBefore="true">
    <bpmn:loopCondition>\${true}</bpmn:loopCondition></bpmn:standardLoopCharacteristics></bpmn:task>`);
  const starts = [];
  definition.getActivityById('work').broker.subscribeTmp('execution', 'execute.start', (_, { content }) => starts.push(content), { noAck: true });
  definition.run();
  await setImmediate();
  assert.equal(starts.filter(content => content.isMultiInstance && !content.isRootScope).length, 0);
  assert.equal(definition.counters.completed, 0, 'Upstream now completes empty loops: remove the asys workaround');
  assert.equal(definition.isRunning, true);
});

test('P3: a loop can complete its activity by publishing execute.completed with output', async t => {
  // src/loop.mjs uses this completion protocol for an empty standard loop.
  class EmptyLoop {
    constructor(activity) { this.activity = activity; }
    execute(message) {
      this.activity.broker.publish('execution', 'execute.completed', { ...message.content, output: [] });
    }
  }
  const definition = await engine(t, `<bpmn:task id="work">${multi}</bpmn:task>`,
    { extend: mapper => { mapper['bpmn:MultiInstanceLoopCharacteristics'] = EmptyLoop; } });
  let output;
  definition.getActivityById('work').on('end', api => { output = api.content.output; });
  definition.run();
  assert.deepEqual(output, []);
  assert.equal(definition.counters.completed, 1);
});

test('P4: recovering a waiting loop redelivers its execute message', async t => {
  // src/loop.mjs must not short-circuit an already executing loop on recovery.
  const messages = [];
  class Loop extends Elements.MultiInstanceLoopCharacteristics {
    execute(message) { messages.push(structuredClone(message)); return super.execute(message); }
  }
  const options = { extend: mapper => { mapper['bpmn:MultiInstanceLoopCharacteristics'] = Loop; } };
  const body = `<bpmn:userTask id="work">${multi}</bpmn:userTask>`;
  const original = await engine(t, body, options);
  original.run();
  original.stop();
  const recovered = await engine(t, body, options);
  let waiting;
  recovered.broker.subscribeTmp('event', 'activity.wait', (_, { content }) => { waiting = content; }, { noAck: true });
  recovered.recover(JSON.parse(JSON.stringify(original.getState()))).resume();
  assert.equal(messages[0].fields.redelivered, undefined);
  assert.ok(messages.slice(1).some(message => message.fields.redelivered === true));
  assert.equal(messages.at(-1).content.executionId, messages[0].content.executionId);
  recovered.signal({ id: 'work', executionId: waiting.executionId });
  assert.equal(recovered.counters.completed, 1);
});

test('P5: suppressing flow.take leaves the target idle while the source and process complete', async t => {
  // src/ad-hoc-elements.mjs intercepts flows; synthetic outbound flows make
  // the coordinator isEnd=false, which must not prevent process completion.
  const enabled = [];
  class SelectedFlow extends Elements.SequenceFlow {
    take(content) { enabled.push({ flow: this, content }); return true; }
    discard() {}
  }
  const definition = await engine(t, `<bpmn:task id="source"/><bpmn:task id="target"/>${flow('next', 'source', 'target')}`,
    { extend: mapper => { mapper['bpmn:SequenceFlow'] = SelectedFlow; } });
  const source = definition.getActivityById('source');
  const target = definition.getActivityById('target');
  assert.equal(source.isEnd, false);
  assert.equal(target.isStart, false);
  definition.run();
  assert.equal(enabled.length, 1);
  assert.ok(enabled[0].flow instanceof SelectedFlow);
  assert.equal(source.counters.taken, 1);
  assert.equal(target.counters.taken, 0);
  assert.equal(target.status, undefined);
  assert.equal(definition.counters.completed, 1);
});

test('P6: omitting serialized I/O and properties prevents BpmnIO attachment', async t => {
  // engine.mjs adaptDefinition deletes these projections so data.mjs owns
  // the preserved, potentially multiple-source associations.
  const definition = await serialized(`<bpmn:task id="work"><bpmn:property id="property"/>
    <bpmn:ioSpecification><bpmn:dataInput id="input"/><bpmn:inputSet/><bpmn:outputSet/></bpmn:ioSpecification></bpmn:task>`);
  const behaviour = definition.activities.find(activity => activity.id === 'work').behaviour;
  assert.equal(behaviour.ioSpecification.type, 'bpmn:InputOutputSpecification');
  assert.ok(behaviour.properties);
  const withIO = await engine(t, '', { definition: structuredClone(definition) });
  assert.ok(withIO.context.loadExtensions(withIO.getActivityById('work')).extensions.some(extension => extension.type === 'bpmnio'));
  delete behaviour.ioSpecification;
  delete behaviour.properties;
  const withoutIO = await engine(t, '', { definition });
  assert.equal(withoutIO.context.loadExtensions(withoutIO.getActivityById('work')), undefined);
});

test('P7: activity.run carries coordinator fields and inbound content into execute', async t => {
  // ad-hoc.mjs starts selected activities through run(content).
  const definition = await engine(t, '<bpmn:userTask id="work"/>');
  const activity = definition.getActivityById('work');
  let execute;
  activity.broker.subscribeTmp('execution', 'execute.start', (_, message) => { execute = message; }, { noAck: true });
  const content = { message: { requested: true }, asysController: 'controller', asysAction: 'selection', inbound: [{ id: 'flow', sourceId: 'prior' }] };
  activity.run(content);
  t.after(() => activity.stop());
  for (const key of Object.keys(content)) assert.deepEqual(execute.content[key], content[key]);
});

test('P8: conditional expressions use environment.resolveExpression', async t => {
  // engine.mjs normalizes language-less conditions to an asys FEEL expression.
  const calls = [];
  const definition = await engine(t, `<bpmn:intermediateCatchEvent id="condition"><bpmn:conditionalEventDefinition>
    <bpmn:condition>ready</bpmn:condition></bpmn:conditionalEventDefinition></bpmn:intermediateCatchEvent>`, {
    environment: { expressions: { resolveExpression(source, scope) { calls.push({ source, scope }); return true; } } },
  });
  definition.run();
  assert.equal(calls[0].source, 'ready');
  assert.equal(calls[0].scope.content.id, 'condition');
  assert.equal(definition.counters.completed, 1);
});

test('P8s: a conditional script uses the return value of scripts.register', async t => {
  // expressions.mjs implements register for conditional events, avoiding the
  // former script-to-expression rewrite in engine.mjs.
  const registered = [];
  let evaluated = 0;
  const definition = await engine(t, `<bpmn:intermediateCatchEvent id="condition"><bpmn:conditionalEventDefinition>
    <bpmn:condition xsi:type="bpmn:tFormalExpression" language="feel">ready</bpmn:condition></bpmn:conditionalEventDefinition></bpmn:intermediateCatchEvent>`, {
    environment: { scripts: {
      register(owner) {
        if (owner.type !== 'bpmn:ConditionalEventDefinition') return;
        registered.push(owner);
        return { execute(scope, callback) { evaluated++; callback(null, scope.environment.variables.ready); } };
      },
      getScript() {},
    }, variables: { ready: true } },
  });
  definition.run();
  assert.equal(registered.length, 1);
  assert.deepEqual(registered[0].behaviour, { scriptFormat: 'feel', script: 'ready' });
  assert.equal(evaluated, 1);
  assert.equal(definition.counters.completed, 1);
});

test('P9: multi-instance execution events identify each iteration and its parent', async t => {
  // runtime.mjs extension maintains loop counters from these broker messages.
  const definition = await engine(t, `<bpmn:userTask id="work">${multi.replace('>1<', '>2<')}</bpmn:userTask>`);
  const activity = definition.getActivityById('work');
  const events = [];
  activity.broker.subscribeTmp('execution', 'execute.*', (key, { content }) => {
    // The root also has isMultiInstance, but no iteration index.
    if (content.isMultiInstance && !content.isRootScope && ['execute.start', 'execute.completed', 'execute.discard'].includes(key)) events.push({ key, content: structuredClone(content) });
  }, { noAck: true, priority: 500 });
  definition.run();
  const starts = events.filter(event => event.key === 'execute.start');
  assert.deepEqual(starts.map(event => event.content.index), [0, 1]);
  activity.getApi({ content: starts[0].content }).signal({ done: true });
  activity.getApi({ content: starts[1].content }).discard();
  for (const [key, index] of [['execute.completed', 0], ['execute.discard', 1]]) {
    const event = events.find(event => event.key === key);
    assert.ok(event, key);
    assert.equal(event.content.index, index);
    assert.equal(event.content.isMultiInstance, true);
    assert.equal(event.content.parent.executionId, activity.executionId);
  }
  assert.equal(definition.counters.completed, 1);
});

test('P10: native standard-loop conditions mean stop, before or after the body', async t => {
  // engine.mjs negates BPMN standard-loop conditions (which mean continue).
  for (const [testBefore, stop, iterations] of [[true, true, 0], [true, false, 2], [false, true, 1], [false, false, 2]]) {
    const definition = await engine(t, `<bpmn:task id="work"><bpmn:standardLoopCharacteristics testBefore="${testBefore}" loopMaximum="2">
      <bpmn:loopCondition>\${${stop}}</bpmn:loopCondition></bpmn:standardLoopCharacteristics></bpmn:task>`);
    const starts = [];
    definition.getActivityById('work').broker.subscribeTmp('execution', 'execute.start', (_, { content }) => {
      if (content.isMultiInstance && !content.isRootScope) starts.push(content);
    }, { noAck: true });
    definition.run();
    assert.equal(starts.length, iterations);
  }
});

test('P11: serialized loop expressions, collection, and element variable reach the native loop', async t => {
  // engine.mjs adaptDefinition normalizes these serialized fields and maps
  // loopDataInputRef to a collection expression with the item variable.
  const definition = await serialized(`<bpmn:task id="work"><bpmn:multiInstanceLoopCharacteristics isSequential="true">
    <bpmn:loopCardinality>limit</bpmn:loopCardinality><bpmn:completionCondition>finished</bpmn:completionCondition>
    </bpmn:multiInstanceLoopCharacteristics></bpmn:task>`);
  const loop = definition.activities[0].behaviour.loopCharacteristics;
  assert.equal(loop.type, 'bpmn:MultiInstanceLoopCharacteristics');
  assert.equal(loop.behaviour.loopCardinality, 'limit');
  assert.equal(loop.behaviour.completionCondition, 'finished');
  loop.behaviour.collection = 'collection';
  loop.behaviour.elementVariable = 'item';
  const calls = [];
  const instance = await engine(t, '', { definition, environment: {
    expressions: { resolveExpression(source, { content }) {
      calls.push({ source, content });
      return { limit: 2, collection: ['a', 'b'], finished: true }[source];
    } },
  } });
  instance.run();
  assert.ok(calls.some(call => call.source === 'limit'));
  assert.ok(calls.some(call => call.source === 'collection'));
  assert.equal(calls.find(call => call.source === 'finished').content.item, 'a');
  assert.equal(instance.counters.completed, 1);
});

test('P12: sequence-flow condition language selects environment.scripts', async t => {
  // engine.mjs adaptDefinition sets conditionExpression.language to feel.
  let evaluated = false;
  const definition = await engine(t, `<bpmn:task id="source"/><bpmn:task id="target"/>${flow('next', 'source', 'target', 'ready')}`, {
    environment: { scripts: { register() {}, getScript(language, owner) {
      assert.equal(language, 'feel');
      assert.equal(owner.behaviour.conditionExpression.body, 'ready');
      return { execute(_scope, callback) { evaluated = true; callback(null, true); } };
    } } },
  });
  definition.run();
  assert.equal(evaluated, true);
  assert.equal(definition.getActivityById('target').counters.taken, 1);
  assert.equal(definition.counters.completed, 1);
});

test('P13: active and idle discards emit their distinct events with selection fields', async t => {
  // ad-hoc.mjs actionEvent must resolve a discarded selection instead of
  // leaving the coordinator waiting on an action recorded as running.
  for (const active of [true, false]) {
    const definition = await engine(t, '<bpmn:userTask id="work"/>');
    const activity = definition.getActivityById('work');
    const events = [];
    activity.broker.subscribeTmp('event', 'activity.#', (key, { content }) => events.push({ key, content }), { noAck: true });
    const content = { asysController: 'controller', asysAction: 'selection' };
    if (active) { activity.run(content); activity.getApi().discard(); }
    else activity.discard(content);
    const discarded = events.find(event => event.key === (active ? 'activity.execution.discard' : 'activity.discard'));
    assert.ok(discarded);
    assert.equal(discarded.content.asysController, 'controller');
    assert.equal(discarded.content.asysAction, 'selection');
    assert.equal(activity.counters.discarded, 1);
  }
});

test('P14: compensation associations prevent automatic starts and deliver compensation', async t => {
  // bpmn.mjs requires this trigger for ad-hoc compensation handlers;
  // engine.mjs leaves their native association-based execution intact.
  let completeWork;
  const definition = await engine(t, `<bpmn:adHocSubProcess id="scope"><bpmn:serviceTask id="work" implementation="\${environment.services.work}"/>
    <bpmn:boundaryEvent id="compensate" attachedToRef="work" cancelActivity="false"><bpmn:compensateEventDefinition/></bpmn:boundaryEvent>
    <bpmn:task id="undo" isForCompensation="true"/>
    <bpmn:association id="handler" sourceRef="compensate" targetRef="undo" associationDirection="One"/>
    <bpmn:intermediateThrowEvent id="rollback"><bpmn:compensateEventDefinition/></bpmn:intermediateThrowEvent>
    ${flow('done', 'work', 'rollback')}</bpmn:adHocSubProcess>`,
  { environment: { services: { work(_, callback) { completeWork = callback; } } } });
  assert.equal(definition.getActivityById('undo').isStart, false);
  const completed = [];
  definition.broker.subscribeTmp('event', 'activity.end', (_, { content }) => completed.push(content.id), { noAck: true });
  definition.run();
  assert.equal(completed.includes('undo'), false);
  completeWork(null, {});
  assert.equal(completed.filter(id => id === 'undo').length, 1);
  assert.equal(definition.counters.completed, 1);
});
