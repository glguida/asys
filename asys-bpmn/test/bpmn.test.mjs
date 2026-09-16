import test from 'node:test';
import assert from 'node:assert/strict';
import { parseWorkflow, validateArtifact, validateExecutable, indexElements } from '../src/bpmn.mjs';
import { workflow, flow } from './helpers.mjs';

const ns = `xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:asys="urn:asys:workflow:1"
  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI"`;
const definitions = body => `<bpmn:definitions ${ns} id="Definitions" targetNamespace="urn:asys:test" expressionLanguage="feel">${body}</bpmn:definitions>`;
const job = (type = 'worker', attrs = '') => `<bpmn:extensionElements><asys:job type="${type}" ${attrs}/></bpmn:extensionElements>`;

test('binds arbitrary job types and keeps original XML, diagram geometry, and references', async () => {
  const xml = definitions(`<bpmn:process id="process" isExecutable="true">
    <bpmn:startEvent id="start"/><bpmn:serviceTask id="research" name="Research">${job('researcher', `args='= ["--model", model]' input='= {question: question}' result="report"`)}</bpmn:serviceTask>
    <bpmn:endEvent id="end"/>${flow('a', 'start', 'research')}${flow('b', 'research', 'end')}
    </bpmn:process><bpmndi:BPMNDiagram id="Diagram"><bpmndi:BPMNPlane id="Plane" bpmnElement="process">
      <bpmndi:BPMNShape id="Shape" bpmnElement="research"><dc:Bounds x="100" y="80" width="120" height="80"/></bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Edge" bpmnElement="a"><di:waypoint x="30" y="120"/><di:waypoint x="100" y="120"/></bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane></bpmndi:BPMNDiagram>`);
  const parsed = validateExecutable(await parseWorkflow(xml));
  assert.equal(parsed.source, xml);
  assert.equal(parsed.id, (await parseWorkflow(xml)).id);
  assert.deepEqual(parsed.bindings.research, { type: 'researcher', args: '= ["--model", model]', input: '= {question: question}', result: 'report' });
  const index = indexElements(parsed.document);
  assert.deepEqual(index.get('a').sourceRef, { $ref: 'start' });
  assert.deepEqual(index.get('Shape').bpmnElement, { $ref: 'research' });
  assert.equal(index.get('Shape').bounds.width, 120);
  assert.equal(index.get('Edge').waypoint.length, 2);
  assert.deepEqual(parsed.processes, [{ id: 'process', name: 'process', isExecutable: true }]);
});

test('parses ordinary unbound diagrams; execution requires explicit job bindings', async () => {
  const parsed = await parseWorkflow(workflow('<bpmn:task id="task"/>'));
  assert.deepEqual(parsed.bindings, {});
  assert.throws(() => validateExecutable(parsed), /task: missing asys:job/);
  const drawing = await parseWorkflow(definitions('<bpmn:process id="drawing"><bpmn:task id="task"/></bpmn:process>'));
  assert.throws(() => validateExecutable(drawing), /no executable process/);
});

test('preserves human resource assignments, documentation, data inputs, and multiple data sources', async () => {
  const xml = definitions(`<bpmn:itemDefinition id="Text" structureRef="xsi:string"/>
    <bpmn:process id="process" isExecutable="true"><bpmn:dataObject id="first" itemSubjectRef="Text"/>
    <bpmn:dataObject id="second" itemSubjectRef="Text"/>
    <bpmn:userTask id="review" name="Review"> <bpmn:documentation>Read the report and decide.</bpmn:documentation>${job('approval')}
      <bpmn:ioSpecification><bpmn:dataInput id="subject" name="subject" itemSubjectRef="Text"/>
        <bpmn:dataOutput id="decision" name="decision"/><bpmn:inputSet id="inputs"><bpmn:dataInputRefs>subject</bpmn:dataInputRefs></bpmn:inputSet>
        <bpmn:outputSet id="outputs"><bpmn:dataOutputRefs>decision</bpmn:dataOutputRefs></bpmn:outputSet></bpmn:ioSpecification>
      <bpmn:dataInputAssociation id="combine"><bpmn:sourceRef>first</bpmn:sourceRef><bpmn:sourceRef>second</bpmn:sourceRef><bpmn:targetRef>subject</bpmn:targetRef>
        <bpmn:transformation xsi:type="bpmn:tFormalExpression" language="feel">first + second</bpmn:transformation></bpmn:dataInputAssociation>
      <bpmn:humanPerformer id="assignee"><bpmn:resourceAssignmentExpression><bpmn:formalExpression language="feel">"alice"</bpmn:formalExpression></bpmn:resourceAssignmentExpression></bpmn:humanPerformer>
      <bpmn:potentialOwner id="owners"><bpmn:resourceAssignmentExpression><bpmn:formalExpression language="feel">["alice", "bob"]</bpmn:formalExpression></bpmn:resourceAssignmentExpression></bpmn:potentialOwner>
    </bpmn:userTask></bpmn:process>`);
  const parsed = validateExecutable(await parseWorkflow(xml));
  const index = indexElements(parsed.document);
  assert.equal(index.get('review').documentation[0].text, 'Read the report and decide.');
  assert.equal(index.get('review').resources.length, 2);
  assert.deepEqual(index.get('combine').sourceRef, [{ $ref: 'first' }, { $ref: 'second' }]);
  assert.equal(index.get('combine').transformation.body, 'first + second');
  assert.deepEqual(index.get('subject').itemSubjectRef, { $ref: 'Text' });
});

test('keeps nested scopes, call activities, standard and multiple-instance loops, and shell script bodies', async () => {
  const parsed = validateExecutable(await parseWorkflow(definitions(`<bpmn:process id="main" isExecutable="true">
    <bpmn:subProcess id="nested"><bpmn:scriptTask id="shell" scriptFormat="sh">${job('shell')}
      <bpmn:script><![CDATA[printf '%s\n' 'literal $HOME; $(example)' ]]></bpmn:script>
      <bpmn:standardLoopCharacteristics testBefore="true" loopMaximum="5"><bpmn:loopCondition xsi:type="bpmn:tFormalExpression" language="feel">attempts &lt; 5</bpmn:loopCondition></bpmn:standardLoopCharacteristics>
    </bpmn:scriptTask></bpmn:subProcess><bpmn:callActivity id="call" calledElement="child"/>
    ${flow('next', 'nested', 'call')}</bpmn:process>
    <bpmn:process id="child"><bpmn:serviceTask id="each">${job('worker')}
      <bpmn:multiInstanceLoopCharacteristics isSequential="false"><bpmn:loopCardinality xsi:type="bpmn:tFormalExpression">3</bpmn:loopCardinality>
      <bpmn:completionCondition xsi:type="bpmn:tFormalExpression">nrOfCompletedInstances = 3</bpmn:completionCondition></bpmn:multiInstanceLoopCharacteristics>
    </bpmn:serviceTask></bpmn:process>`)));
  const index = indexElements(parsed.document);
  assert.equal(index.get('call').calledElement, 'child');
  assert.match(index.get('shell').script, /literal \$HOME; \$\(example\)/);
  assert.equal(index.get('shell').loopCharacteristics.testBefore, true);
  assert.equal(index.get('each').loopCharacteristics.loopCardinality.body, '3');
});

test('preserves ad-hoc completion conditions, ordering, connected activities, and boundary events', async () => {
  const parsed = validateExecutable(await parseWorkflow(workflow(`<bpmn:adHocSubProcess id="work" ordering="Sequential" cancelRemainingInstances="false">${job('coordinator')}
    <bpmn:serviceTask id="read.file">${job('reader')}</bpmn:serviceTask>
    <bpmn:userTask id="review">${job('approval')}</bpmn:userTask>${flow('follow', 'read.file', 'review')}
    <bpmn:boundaryEvent id="deadline" attachedToRef="review"><bpmn:timerEventDefinition><bpmn:timeDuration xsi:type="bpmn:tFormalExpression">PT1H</bpmn:timeDuration></bpmn:timerEventDefinition></bpmn:boundaryEvent>
    <bpmn:completionCondition xsi:type="bpmn:tFormalExpression">approved = true</bpmn:completionCondition>
    </bpmn:adHocSubProcess>`)));
  const scope = indexElements(parsed.document).get('work');
  assert.equal(scope.completionCondition.body, 'approved = true');
  assert.equal(scope.cancelRemainingInstances, false);
  assert.equal(scope.ordering, 'Sequential');
  assert.ok(Object.hasOwn(parsed.bindings, 'read.file'));
});

test('preserves collaboration lanes, messages, signals, errors, and catch/throw event definitions', async () => {
  const parsed = validateExecutable(await parseWorkflow(definitions(`<bpmn:message id="message" name="ready"/><bpmn:signal id="signal" name="wake"/>
    <bpmn:error id="error" errorCode="FAILED"/><bpmn:escalation id="escalation" escalationCode="HELP"/>
    <bpmn:process id="main" isExecutable="true"><bpmn:laneSet id="lanes"><bpmn:lane id="lane"><bpmn:flowNodeRef>receive</bpmn:flowNodeRef></bpmn:lane></bpmn:laneSet>
      <bpmn:receiveTask id="receive" messageRef="message"/>
      <bpmn:intermediateCatchEvent id="catch"><bpmn:signalEventDefinition signalRef="signal"/></bpmn:intermediateCatchEvent>
      <bpmn:intermediateThrowEvent id="throw"><bpmn:escalationEventDefinition escalationRef="escalation"/></bpmn:intermediateThrowEvent>
      <bpmn:endEvent id="failed"><bpmn:errorEventDefinition errorRef="error"/></bpmn:endEvent>
      <bpmn:boundaryEvent id="timer" attachedToRef="receive" cancelActivity="false"><bpmn:timerEventDefinition><bpmn:timeCycle xsi:type="bpmn:tFormalExpression">R3/PT1M</bpmn:timeCycle></bpmn:timerEventDefinition></bpmn:boundaryEvent>
    </bpmn:process><bpmn:collaboration id="collaboration"><bpmn:participant id="pool" processRef="main"/><bpmn:participant id="external"/>
      <bpmn:messageFlow id="delivery" sourceRef="external" targetRef="receive" messageRef="message"/></bpmn:collaboration>`)));
  const index = indexElements(parsed.document);
  assert.deepEqual(index.get('pool').processRef, { $ref: 'main' });
  assert.deepEqual(index.get('lane').flowNodeRef, [{ $ref: 'receive' }]);
  assert.deepEqual(index.get('delivery').messageRef, { $ref: 'message' });
  assert.equal(index.get('failed').eventDefinitions[0].$type, 'bpmn:ErrorEventDefinition');
});

test('retains FEEL gateway conditions and distinguishes parsing from expression-language support', async () => {
  const xml = workflow(`<bpmn:exclusiveGateway id="choice" default="fallback"/><bpmn:endEvent id="yes"/><bpmn:endEvent id="no"/>
    ${flow('chosen', 'choice', 'yes', 'approved = true')}${flow('fallback', 'choice', 'no')}`);
  const parsed = validateExecutable(await parseWorkflow(xml));
  assert.equal(indexElements(parsed.document).get('chosen').conditionExpression.body, 'approved = true');
  const foreign = await parseWorkflow(xml.replace('language="feel"', 'language="xpath"'));
  assert.throws(() => validateExecutable(foreign), /must use FEEL/);
});

test('foreign editor extensions survive parsing and are rejected as unsupported execution bindings', async () => {
  const xml = workflow('<bpmn:task id="task"><bpmn:extensionElements><editor:metadata xmlns:editor="urn:editor" color="blue"><editor:value>visible</editor:value></editor:metadata><asys:job type="worker"/></bpmn:extensionElements></bpmn:task>');
  const parsed = await parseWorkflow(xml);
  assert.match(JSON.stringify(parsed.document), /visible/);
  assert.throws(() => validateExecutable(parsed), /unsupported execution extension editor:metadata/);
});

test('rejects malformed XML, duplicate and unresolved IDs, unsafe declarations, and invalid references', async () => {
  for (const [xml, pattern] of [
    ['<bpmn:definitions>', /unparsable|unclosed|namespace|definitions|end of file/i],
    [workflow('<bpmn:startEvent id="same"/><bpmn:endEvent id="same"/>'), /duplicate/i],
    [workflow('<bpmn:startEvent id="start"/>' + flow('broken', 'start', 'missing')), /unresolved|source and target/i],
    ['<!DOCTYPE definitions [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + workflow(''), /DTD/],
    [workflow('<bpmn:startEvent id="a"/><bpmn:subProcess id="sub"><bpmn:endEvent id="b"/></bpmn:subProcess>' + flow('cross', 'a', 'b')), /crosses/],
    [workflow('<bpmn:boundaryEvent id="boundary"/>'), /attached activity/],
    [workflow('<bpmn:intermediateCatchEvent id="timer"><bpmn:timerEventDefinition/></bpmn:intermediateCatchEvent>'), /exactly one/],
    [workflow('<bpmn:endEvent id="end"/><bpmn:task id="task"/>' + flow('bad', 'end', 'task')), /end events/],
    [workflow('<bpmn:task id="task"/><bpmn:startEvent id="start"/>' + flow('bad', 'task', 'start')), /start and boundary/],
    [definitions('<bpmn:signal id="signal"/><bpmn:process id="process"><bpmn:receiveTask id="receive" messageRef="signal"/></bpmn:process>'), /must reference bpmn:Message/],
  ]) await assert.rejects(parseWorkflow(xml), pattern);
});

test('rejects invalid job bindings, while allowing BPMN IDs independent of filesystem names', async () => {
  for (const [body, pattern] of [
    [`<bpmn:task id="task">${job('../worker')}</bpmn:task>`, /valid name/],
    [`<bpmn:task id="task">${job('worker', 'input="{}"')}</bpmn:task>`, /beginning with/],
    [`<bpmn:task id="task">${job('worker', 'args="= ["')}</bpmn:task>`, /invalid FEEL/],
    [`<bpmn:task id="task">${job('worker', 'result="__proto__"')}</bpmn:task>`, /reserved/],
    [`<bpmn:exclusiveGateway id="gateway">${job()}</bpmn:exclusiveGateway>`, /belongs on a task/],
    ['<bpmn:task id="task"><bpmn:extensionElements><asys:job type="one"/><asys:job type="two"/></bpmn:extensionElements></bpmn:task>', /one asys:job/],
    ['<bpmn:serviceTask id="task"><bpmn:extensionElements><asys:agent model="old"/></bpmn:extensionElements></bpmn:serviceTask>', /unknown|unparsable/i],
  ]) await assert.rejects(parseWorkflow(workflow(body)), pattern);
  const parsed = validateExecutable(await parseWorkflow(workflow(`<bpmn:task id="étude.long-id">${job('worker')}</bpmn:task><bpmn:endEvent id="constructor"/>${flow('sortie', 'étude.long-id', 'constructor')}`)));
  assert.ok(Object.hasOwn(parsed.bindings, 'étude.long-id'));
  assert.deepEqual(indexElements(parsed.document).get('sortie').targetRef, { $ref: 'constructor' });
});

test('saved parsed models detect accidental or partial modification', async () => {
  const parsed = await parseWorkflow(workflow('<bpmn:startEvent id="start"/>'));
  assert.equal(validateArtifact(JSON.parse(JSON.stringify(parsed))).id, parsed.id);
  parsed.document.rootElements[0].id = 'changed';
  assert.throws(() => validateArtifact(parsed), /digest/);
});

test('resolves qualified references in the target namespace, including locally declared prefixes', async () => {
  const xml = definitions(`<bpmn:message id="ready"/><bpmn:process id="main" isExecutable="true">
    <bpmn:receiveTask id="receive" xmlns:local="urn:asys:test" messageRef="local:ready"/>
    <bpmn:callActivity id="call" xmlns:local="urn:asys:test" calledElement="local:child"/>
    <bpmn:sequenceFlow id="next" sourceRef="receive" targetRef="call"/>
    </bpmn:process><bpmn:process id="child"><bpmn:task id="work">${job('2-workers')}</bpmn:task></bpmn:process>`);
  const parsed = validateExecutable(await parseWorkflow(xml));
  const index = indexElements(parsed.document);
  assert.deepEqual(index.get('receive').messageRef, { $ref: 'ready' });
  assert.equal(index.get('call').calledElement, 'child');
  assert.equal(parsed.bindings.work.type, '2-workers');
});

test('unrelated nonexecutable diagrams need no job bindings', async () => {
  validateExecutable(await parseWorkflow(definitions(`<bpmn:process id="main" isExecutable="true"><bpmn:startEvent id="start"/></bpmn:process>
    <bpmn:process id="drawing"><bpmn:task id="unbound"/></bpmn:process>`)));
});
