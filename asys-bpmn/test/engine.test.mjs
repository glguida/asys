import test from 'node:test';
import assert from 'node:assert/strict';
import * as Elements from 'bpmn-elements';
import { parseWorkflow } from '../src/bpmn.mjs';
import { prepareEngine, executionContext } from '../src/engine.mjs';
import { expressions, scripts } from '../src/expressions.mjs';
import { AsysAdHocFlow } from '../src/ad-hoc-elements.mjs';
import { digest } from '../src/values.mjs';
import { workflow, flow } from './helpers.mjs';

const job = '<bpmn:extensionElements><asys:job type="worker"/></bpmn:extensionElements>';

async function engine(t, body, { variables = {}, Service, Coordinator, enable = () => {} } = {}) {
  const artifact = await parseWorkflow(workflow(body));
  const prepared = await prepareEngine(artifact);
  Service ??= function Service() { this.execute = (message, callback) => callback(null, message.content.item ?? 'done'); };
  const context = executionContext(prepared, artifact.bindings, Service, Coordinator ?? Service, enable);
  const definition = new Elements.Definition(Elements.Context(context, new Elements.Environment({ variables, expressions: expressions(), scripts: scripts() })));
  t.after(() => definition.stop());
  return { artifact, prepared, definition };
}

for (const middle of ['<bpmn:exclusiveGateway id="middle"/>', `<bpmn:intermediateCatchEvent id="middle"><bpmn:conditionalEventDefinition>
  <bpmn:condition xsi:type="bpmn:tFormalExpression">true</bpmn:condition></bpmn:conditionalEventDefinition></bpmn:intermediateCatchEvent>`]) {
  test(`ad-hoc control nodes execute without coordinator selection: ${middle.match(/<bpmn:(\w+)/u)[1]}`, async t => {
    let context;
    const enabled = [];
    function Coordinator(activity) { this.execute = () => { context = activity.context; }; }
    const { definition } = await engine(t, `<bpmn:adHocSubProcess id="work">${job}
      <bpmn:task id="first">${job}</bpmn:task>${middle}<bpmn:task id="last">${job}</bpmn:task>
      ${flow('one', 'first', 'middle')}${flow('two', 'middle', 'last')}</bpmn:adHocSubProcess>`,
      { Coordinator, enable: activity => enabled.push(activity.id) });
    definition.run();
    assert.deepEqual(enabled, []);
    context.getActivityById('first').run();
    assert.equal(context.getActivityById('middle').counters.taken, 1);
    assert.deepEqual(enabled, ['last']);
    assert.equal(context.getActivityById('last').counters.taken, 0);
  });
}

test('ad-hoc compensation without a compensation association is rejected before execution', async t => {
  await assert.rejects(engine(t, `<bpmn:adHocSubProcess id="work">${job}
    <bpmn:task id="undo" isForCompensation="true">${job}</bpmn:task></bpmn:adHocSubProcess>`),
  /undo: ad-hoc compensation requires an association from a compensation catch event in the same scope/);
});

test('connected ad-hoc compensation runs only when its compensation event fires', async t => {
  let context;
  const ran = [], enabled = [];
  function Service(activity) { this.execute = (_, callback) => { ran.push(activity.id); callback(null, {}); }; }
  function Coordinator(activity) { this.execute = () => { context = activity.context; }; }
  const { definition } = await engine(t, `<bpmn:adHocSubProcess id="work">${job}
    <bpmn:task id="first">${job}</bpmn:task>
    <bpmn:boundaryEvent id="compensate" attachedToRef="first" cancelActivity="false"><bpmn:compensateEventDefinition/></bpmn:boundaryEvent>
    <bpmn:task id="undo" isForCompensation="true">${job}</bpmn:task>
    <bpmn:association id="handler" sourceRef="compensate" targetRef="undo" associationDirection="One"/>
    <bpmn:intermediateThrowEvent id="rollback"><bpmn:compensateEventDefinition/></bpmn:intermediateThrowEvent>
    ${flow('next', 'first', 'rollback')}</bpmn:adHocSubProcess>`,
    { Service, Coordinator, enable: activity => enabled.push(activity.id) });
  definition.run();
  assert.deepEqual(ran, []);
  assert.equal(context.getActivityById('undo').isStart, false);
  context.getActivityById('first').run();
  assert.deepEqual(ran, ['first', 'undo']);
  assert.deepEqual(enabled, []);
});

test('coordinator completion does not enable or start unselected children through synthetic flows', async t => {
  const ran = [], enabled = [];
  function Service(activity) { this.execute = (_, callback) => { ran.push(activity.id); callback(null, {}); }; }
  function Coordinator() { this.execute = (_, callback) => callback(null, {}); }
  const { definition } = await engine(t, `<bpmn:adHocSubProcess id="work">${job}
    <bpmn:task id="unused">${job}</bpmn:task><bpmn:exclusiveGateway id="gateway"/></bpmn:adHocSubProcess>`,
  { Service, Coordinator, enable: activity => enabled.push(activity.id) });
  definition.run();
  assert.deepEqual(ran, []);
  assert.deepEqual(enabled, []);
  assert.equal(definition.counters.completed, 1);
});

test('registered loops reject invalid cardinalities with the original message and execution source', async t => {
  for (const value of [-1, 1.5, '2', true, null, 9007199254740992]) {
    const { definition } = await engine(t, `<bpmn:task id="work">${job}<bpmn:multiInstanceLoopCharacteristics>
      <bpmn:loopCardinality xsi:type="bpmn:tFormalExpression">count</bpmn:loopCardinality></bpmn:multiInstanceLoopCharacteristics></bpmn:task>`,
      { variables: { count: value } });
    let failure;
    definition.on('error', error => { failure = error; });
    definition.run();
    assert.ok(failure instanceof Elements.RunError, JSON.stringify(value));
    assert.equal(failure.message, 'work: loop cardinality must be a nonnegative integer');
    assert.equal(failure.source.content.id, 'work');
    assert.equal(failure.source.content.isRootScope, true);
  }
});

test('registered collection loops require arrays, including allowing an empty collection', async t => {
  const body = `<bpmn:task id="work">${job}<bpmn:ioSpecification><bpmn:dataInput id="items"/><bpmn:inputSet/><bpmn:outputSet/></bpmn:ioSpecification>
    <bpmn:multiInstanceLoopCharacteristics><bpmn:loopDataInputRef>items</bpmn:loopDataInputRef><bpmn:inputDataItem id="part"/></bpmn:multiInstanceLoopCharacteristics></bpmn:task>`;
  for (const value of [null, 'ab', { 0: 'a', length: 1 }, [], ['a', 'b']]) {
    const { definition } = await engine(t, body, { variables: { inputs: { items: value } } });
    let failure, output;
    definition.on('error', error => { failure = error; });
    definition.getActivityById('work').on('end', api => { output = api.content.output; });
    definition.run();
    if (Array.isArray(value)) {
      assert.equal(failure, undefined);
      assert.deepEqual(output, value);
      assert.equal(definition.counters.completed, 1);
    } else {
      assert.ok(failure instanceof Elements.RunError);
      assert.equal(failure.message, 'work: multi-instance input must be a collection');
    }
  }
});

test('definition adaptation preserves the BPMN artifact and disables native I/O projections', async t => {
  const { artifact, prepared, definition } = await engine(t, `<bpmn:task id="work">${job}<bpmn:property id="property"/>
    <bpmn:ioSpecification><bpmn:dataInput id="input"/><bpmn:inputSet/><bpmn:outputSet/></bpmn:ioSpecification></bpmn:task>`);
  const original = JSON.stringify(artifact);
  assert.ok(artifact.document.rootElements[0].flowElements[0].ioSpecification);
  const behaviour = prepared.activities.find(activity => activity.id === 'work').behaviour;
  assert.equal(behaviour.ioSpecification, undefined);
  assert.equal(behaviour.properties, undefined);
  assert.equal(definition.context.loadExtensions(definition.getActivityById('work')), undefined);
  assert.deepEqual(await prepareEngine(artifact), prepared);
  assert.equal(JSON.stringify(artifact), original);
});

test('synthetic ad-hoc flows avoid collisions and leave special activities and nested scopes alone', async t => {
  const coordinatorId = `_asys_${digest('work').slice(0, 32)}`;
  const syntheticId = `_asys_flow_${digest(['work', 'chosen']).slice(0, 32)}`;
  const { prepared, definition } = await engine(t, `<bpmn:adHocSubProcess id="work">${job}
    <bpmn:task id="chosen">${job}</bpmn:task><bpmn:task id="follow">${job}</bpmn:task>${flow('dependency', 'chosen', 'follow')}
    <bpmn:boundaryEvent id="boundary" attachedToRef="chosen"><bpmn:errorEventDefinition/></bpmn:boundaryEvent>
    <bpmn:boundaryEvent id="compensate" attachedToRef="chosen" cancelActivity="false"><bpmn:compensateEventDefinition/></bpmn:boundaryEvent>
    <bpmn:task id="compensation" isForCompensation="true">${job}</bpmn:task>
    <bpmn:association id="handler" sourceRef="compensate" targetRef="compensation" associationDirection="One"/>
    <bpmn:subProcess id="triggered" triggeredByEvent="true"/>
    <bpmn:subProcess id="nested"><bpmn:task id="inside">${job}</bpmn:task></bpmn:subProcess>
    </bpmn:adHocSubProcess><bpmn:task id="${coordinatorId}">${job}</bpmn:task><bpmn:task id="${syntheticId}">${job}</bpmn:task>`);
  const coordinator = prepared.activities.find(activity => activity.behaviour.asysCoordinator);
  assert.equal(coordinator.id, coordinatorId + '_');
  const flows = prepared.sequenceFlows.filter(flow => flow.behaviour.asysAdHoc);
  assert.deepEqual(flows.map(flow => flow.targetId).sort(), ['chosen', 'follow', 'nested']);
  assert.equal(flows.find(flow => flow.targetId === 'chosen').id, syntheticId + '_');
  const ids = [...prepared.activities, ...prepared.sequenceFlows].map(element => element.id);
  assert.equal(new Set(ids).size, ids.length);
  const chosen = definition.getActivityById('chosen');
  assert.equal(chosen.isStart, false);
  assert.equal(Object.hasOwn(chosen, 'isStart'), false);
  assert.equal(Object.hasOwn(chosen, 'run'), false);
  assert.equal(definition.getActivityById('inside').isStart, true);
  assert.ok(definition.context.getSequenceFlowById('dependency') instanceof AsysAdHocFlow);
});

for (const language of ['', ' language="feel"', ' language="https://www.omg.org/spec/DMN/20191111/FEEL/"']) {
  test(`conditional events evaluate FEEL with ${language || 'the default language'}`, async t => {
    const { definition, prepared } = await engine(t, `<bpmn:intermediateCatchEvent id="condition"><bpmn:conditionalEventDefinition>
      <bpmn:condition xsi:type="bpmn:tFormalExpression"${language}>message.ready = true</bpmn:condition>
      </bpmn:conditionalEventDefinition></bpmn:intermediateCatchEvent>`);
    const event = prepared.activities[0].behaviour.eventDefinitions[0];
    if (language) {
      assert.equal(event.behaviour.script.body, 'message.ready = true');
      assert.equal(event.behaviour.expression, undefined);
    } else assert.equal(event.behaviour.expression, '= message.ready = true');
    definition.run();
    assert.equal(definition.isRunning, true);
    definition.signal({ id: 'condition', ready: false });
    assert.equal(definition.isRunning, true);
    definition.signal({ id: 'condition', ready: true });
    assert.equal(definition.counters.completed, 1);
  });
}

test('conditional scripts require boolean results and cannot assign workflow variables', async t => {
  const { definition } = await engine(t, `<bpmn:intermediateCatchEvent id="condition"><bpmn:conditionalEventDefinition>
    <bpmn:condition xsi:type="bpmn:tFormalExpression" language="feel">{changed: true}</bpmn:condition>
    </bpmn:conditionalEventDefinition></bpmn:intermediateCatchEvent>`);
  let failure;
  definition.on('error', error => { failure = error; });
  definition.run();
  assert.match(failure?.message, /did not return a boolean/);
  assert.equal(definition.environment.variables.changed, undefined);
});
