import moddle from './moddle.json' with { type: 'json' };
import { readBpmn } from './xml.mjs';
import { checkExpression } from './expressions.mjs';
import { digest, object, requiredString } from './values.mjs';

export const FORMAT = 'asys.bpmn/1';
export const NAMESPACE = moddle.uri;
export const FEEL_LANGUAGES = new Set(['feel', 'https://www.omg.org/spec/DMN/20191111/FEEL/']);
export const JOB_TASKS = new Set([
  'bpmn:Task', 'bpmn:ServiceTask', 'bpmn:UserTask', 'bpmn:ManualTask',
  'bpmn:ScriptTask', 'bpmn:SendTask', 'bpmn:BusinessRuleTask', 'bpmn:ReceiveTask',
]);
const SCOPES = new Set(['bpmn:Process', 'bpmn:SubProcess', 'bpmn:AdHocSubProcess', 'bpmn:Transaction']);
const REFERENCE_TYPES = {
  messageRef: 'bpmn:Message', signalRef: 'bpmn:Signal', errorRef: 'bpmn:Error',
  escalationRef: 'bpmn:Escalation', itemSubjectRef: 'bpmn:ItemDefinition',
  processRef: 'bpmn:Process', dataObjectRef: 'bpmn:DataObject', dataStoreRef: 'bpmn:DataStore',
};

// Parsing is independent of the execution engine and the job runtime. The
// document keeps all BPMN properties, including diagram geometry and typed
// references; executable bindings are a separate, small index over that model.
export async function parseWorkflow(xml) {
  requiredString(xml, 'BPMN source');
  if (Buffer.byteLength(xml) > 8 * 1024 * 1024) throw new Error('BPMN source exceeds 8 MiB');
  if (/<!DOCTYPE|<!ENTITY/iu.test(xml)) throw new Error('BPMN must not contain a DTD or entity declarations');
  const parsed = await readBpmn(xml);
  if (parsed.warnings.length) throw new Error(parsed.warnings.map(w => w.message).join('; '));
  const root = parsed.rootElement;
  if (root.$type !== 'bpmn:Definitions') throw new Error('Expected BPMN definitions');
  const elements = [...walkModdle(root)];
  const ids = new Set();
  const bindings = Object.create(null);
  for (const element of elements) {
    if (element.id) {
      if (ids.has(element.id)) invalid(element, 'duplicate BPMN ID');
      ids.add(element.id);
    }
    validateElement(element);
    const jobs = (element.extensionElements?.values ?? []).filter(e => e.$type === 'asys:Job');
    if (jobs.length > 1) invalid(element, 'use one asys:job binding');
    if (!jobs.length) continue;
    if (!JOB_TASKS.has(element.$type) && element.$type !== 'bpmn:AdHocSubProcess') {
      invalid(element, 'asys:job belongs on a task or ad-hoc subprocess');
    }
    requiredString(element.id, 'Bound activity ID');
    const job = jobs[0];
    jobType(job.type, `${element.id} job type`);
    const input = job.input ?? '= variables';
    const args = job.args ?? '= []';
    for (const [key, value] of Object.entries({ input, args })) {
      if (!value.trimStart().startsWith('=')) invalid(element, `job ${key} must be a FEEL expression beginning with '='`);
      checkExpression(value, `${element.id} job ${key}`);
    }
    const result = job.result ?? element.id;
    requiredString(result, `${element.id} result variable`);
    if (['__proto__', 'prototype', 'constructor', 'variables', 'output', 'message', 'item', 'index', 'fields', 'content', 'properties', '_data'].includes(result)) {
      invalid(element, `result variable ${result} is reserved`);
    }
    bindings[element.id] = { type: job.type, input, args, result };
  }
  const artifact = {
    format: FORMAT, name: root.name ?? root.id ?? 'Workflow',
    source: xml, sourceHash: digest(xml), document: snapshot(root),
    processes: elements.filter(e => e.$type === 'bpmn:Process').map(e => ({
      id: e.id ?? '', name: e.name ?? e.id ?? '', isExecutable: e.isExecutable === true,
    })),
    bindings: { ...bindings },
  };
  artifact.id = digest(artifact);
  return validateArtifact(artifact);
}

export function validateArtifact(artifact) {
  if (!object(artifact) || artifact.format !== FORMAT) throw new Error('Unsupported saved BPMN format');
  const { id, ...body } = artifact;
  if (id !== digest(body) || artifact.sourceHash !== digest(artifact.source)) throw new Error('Saved BPMN digest does not match its contents');
  if (artifact.document?.$type !== 'bpmn:Definitions' || !object(artifact.bindings) || !Array.isArray(artifact.processes)) {
    throw new Error('Saved BPMN has no definitions, processes, or job bindings');
  }
  const index = indexElements(artifact.document);
  for (const [id, binding] of Object.entries(artifact.bindings)) {
    const element = index.get(id);
    if (!element || (!JOB_TASKS.has(element.$type) && element.$type !== 'bpmn:AdHocSubProcess')) throw new Error(`${id}: job binding has no activity`);
    if (!object(binding)) throw new Error(`${id}: invalid job binding`);
    jobType(binding.type, `${id} job type`);
    checkExpression(binding.args, `${id} job args`);
    checkExpression(binding.input, `${id} job input`);
    requiredString(binding.result, `${id} result variable`);
  }
  return artifact;
}

// A diagram can be parsed and inspected before execution bindings are added.
// This check is called when loading a workflow for execution, not by the XML
// parser. It never guesses a worker type from a BPMN name or an agent model.
export function validateExecutable(artifact) {
  validateArtifact(artifact);
  if (!artifact.processes.some(p => p.isExecutable)) throw new Error('Workflow has no executable process');
  const index = indexElements(artifact.document);
  const errors = [];
  const defaultLanguage = artifact.document.expressionLanguage;
  const processes = (artifact.document.rootElements ?? []).filter(e => e.$type === 'bpmn:Process');
  const visited = new Set();
  for (const process of processes.filter(p => p.isExecutable)) visitProcess(process);
  if (errors.length) throw new Error(errors.join('; '));
  return artifact;

  function visitProcess(process) {
    if (visited.has(process.id)) return;
    visited.add(process.id);
    visit(process);
  }

  function visit(element, parent) {
    const type = element.$type;
    const label = element.id ?? type;
    if ((SCOPES.has(type) || JOB_TASKS.has(type) || type === 'bpmn:CallActivity') && !element.id) errors.push(`${type}: executable element needs an ID`);
    const needsJob = JOB_TASKS.has(type) && type !== 'bpmn:ReceiveTask';
    if ((needsJob || type === 'bpmn:AdHocSubProcess') && !Object.hasOwn(artifact.bindings, element.id)) errors.push(`${label}: missing asys:job binding`);
    if (type === 'bpmn:CallActivity') {
      const called = element.calledElement;
      if (!called) errors.push(`${label}: call activity needs calledElement`);
      else if (called.trimStart().startsWith('=')) {
        expression(called, undefined, label);
        for (const candidate of processes) visitProcess(candidate);
      }
      else if (index.get(called)?.$type !== 'bpmn:Process') errors.push(`${label}: called process ${called} is not in this document`);
      else visitProcess(index.get(called));
    }
    if (type === 'bpmn:Assignment') errors.push(`${label}: data-association assignments are not implemented; use a FEEL transformation`);
    if (['bpmn:DataInputAssociation', 'bpmn:DataOutputAssociation'].includes(type) && element.sourceRef?.length > 1 && !element.transformation && !element.assignment?.length) {
      errors.push(`${label}: multiple data sources require a transformation`);
    }
    if (type === 'bpmn:MultiInstanceLoopCharacteristics' && element.behavior && element.behavior !== 'All') errors.push(`${label}: multi-instance event behavior ${element.behavior} is not implemented`);
    for (const extension of element.extensionElements?.values ?? []) {
      if (extension.$type !== 'asys:Job') errors.push(`${label}: unsupported execution extension ${extension.$type}`);
    }
    if (type === 'bpmn:FormalExpression' && (parent?.$type !== 'bpmn:TimerEventDefinition' || element.body?.trimStart().startsWith('='))) {
      // Shell/Python/etc. scripts are data for their worker. BPMN's own control
      // expressions and data associations are evaluated by the workflow side.
      expression(element.body ?? '', element.language, label);
    }
    for (const child of children(element)) visit(child, element);
  }
  function expression(body, language, label) {
    const selected = language ?? defaultLanguage ?? 'feel';
    if (!FEEL_LANGUAGES.has(selected)) { errors.push(`${label}: execution expressions must use FEEL (found ${selected})`); return; }
    try { checkExpression(body, label); } catch (error) { errors.push(error.message); }
  }
}

export function indexElements(document) {
  const index = new Map();
  for (const element of walkDocument(document)) {
    if (!element.id) continue;
    if (index.has(element.id)) throw new Error(`Duplicate BPMN ID ${element.id}`);
    index.set(element.id, element);
  }
  return index;
}

// Structural requirements of one selected process, including subprocesses and
// calls. A dynamic calledElement can select any process in this document.
export function requiredJobTypes(artifact, processId) {
  const index = indexElements(artifact.document);
  const visited = new Set(), types = new Set();
  function visit(id) {
    if (visited.has(id)) return;
    visited.add(id);
    const process = index.get(id);
    if (process?.$type !== 'bpmn:Process') throw new Error(`Unknown process ${id}`);
    for (const element of walkDocument(process)) {
      const binding = artifact.bindings[element.id];
      if (binding) types.add(binding.type);
      if (element.$type !== 'bpmn:CallActivity') continue;
      if (element.calledElement?.trimStart().startsWith('=')) {
        for (const candidate of artifact.processes) visit(candidate.id);
      } else if (element.calledElement) visit(element.calledElement);
    }
  }
  visit(processId);
  return [...types].sort();
}

export function* walkDocument(document) {
  yield document;
  for (const child of children(document)) yield* walkDocument(child);
}

function* children(element) {
  for (const value of Object.values(element)) {
    for (const child of Array.isArray(value) ? value : [value]) {
      if (object(child) && child.$type) yield child;
    }
  }
}

function* walkModdle(element, depth = 0) {
  if (depth > 256) throw new Error('BPMN nesting exceeds 256 levels');
  yield element;
  for (const property of element.$descriptor.properties ?? []) {
    if (property.isReference || property.isVirtual) continue;
    const value = element[property.name];
    for (const child of Array.isArray(value) ? value : [value]) {
      if (child?.$type) yield* walkModdle(child, depth + 1);
    }
  }
  if (element.$descriptor.isGeneric) for (const child of element.$children ?? []) yield* walkModdle(child, depth + 1);
}

function snapshot(element) {
  const result = { $type: element.$type };
  const attributes = { ...element.$attrs };
  if (Object.keys(attributes).length) result.$attrs = attributes;
  for (const property of element.$descriptor.properties ?? []) {
    if (property.isVirtual || !Object.hasOwn(element, property.name)) continue;
    const value = element[property.name];
    if (value === undefined) continue;
    const copy = value => property.isReference ? { $ref: value.id } : value?.$type ? snapshot(value) : value;
    result[property.name] = Array.isArray(value) ? value.map(copy) : copy(value);
  }
  if (element.$descriptor.isGeneric) {
    // Unknown editor extensions are preserved for inspection and round-trips.
    // validateExecutable rejects them rather than inventing their semantics.
    for (const [key, value] of Object.entries(element)) {
      if (key === '$children') result.values = value.map(snapshot);
      else if (key !== '$type' && key !== '$attrs') result[key] = value;
    }
  }
  return result;
}

function invalid(element, message) { throw new Error(`${element.id ?? element.$type}: ${message}`); }
function jobType(value, label) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(value)) throw new Error(`${label} is not a valid name`);
}
function is(element, type) { return element?.$instanceOf?.(type) === true; }

function validateElement(element) {
  const type = element.$type;
  for (const [key, expected] of Object.entries(REFERENCE_TYPES)) {
    const ref = element[key];
    if (ref && !is(ref, expected)) invalid(element, `${key} must reference ${expected}`);
  }
  if (type === 'bpmn:SequenceFlow') {
    const source = element.sourceRef, target = element.targetRef;
    if (!is(source, 'bpmn:FlowNode') || !is(target, 'bpmn:FlowNode')) invalid(element, 'sequence flow needs a source and target flow node');
    if (source.$parent !== target.$parent || source.$parent !== element.$parent) invalid(element, 'sequence flow crosses a process or subprocess boundary');
    if (source.$type === 'bpmn:EndEvent') invalid(element, 'end events cannot have outgoing sequence flows');
    if (['bpmn:StartEvent', 'bpmn:BoundaryEvent'].includes(target.$type)) invalid(element, 'start and boundary events cannot have incoming sequence flows');
  }
  if (is(element, 'bpmn:FlowNode')) {
    for (const flow of element.incoming ?? []) if (flow.targetRef !== element) invalid(element, 'incoming sequence flow targets another node');
    for (const flow of element.outgoing ?? []) if (flow.sourceRef !== element) invalid(element, 'outgoing sequence flow starts at another node');
    if (element.default && element.default.sourceRef !== element) invalid(element, 'default flow must be outgoing from this node');
  }
  if (type === 'bpmn:BoundaryEvent') {
    if (!is(element.attachedToRef, 'bpmn:Activity')) invalid(element, 'boundary event needs an attached activity');
    if (element.attachedToRef.$parent !== element.$parent) invalid(element, 'boundary event and attached activity must share a scope');
  }
  if (type === 'bpmn:TimerEventDefinition' && ['timeDate', 'timeDuration', 'timeCycle'].filter(k => element[k]).length !== 1) {
    invalid(element, 'timer needs exactly one of timeDate, timeDuration, or timeCycle');
  }
  if (type === 'bpmn:AdHocSubProcess') {
    if (!['Parallel', 'Sequential'].includes(element.ordering ?? 'Parallel')) invalid(element, 'invalid ad-hoc ordering');
    if (element.flowElements?.some(e => ['bpmn:StartEvent', 'bpmn:EndEvent'].includes(e.$type))) invalid(element, 'ad-hoc subprocesses cannot contain start or end events');
  }
  if (is(element, 'bpmn:DataAssociation') && !element.targetRef) invalid(element, 'data association needs a target');
  if (type === 'asys:Job' && element.$parent?.$type !== 'bpmn:ExtensionElements') invalid(element, 'job must be inside extensionElements');
}
