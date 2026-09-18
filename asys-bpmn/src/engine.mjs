import serializer, { deserialize, TypeResolver } from 'moddle-context-serializer';
import * as Elements from 'bpmn-elements';
import { readBpmn } from './xml.mjs';
import { validateExecutable, indexElements, adHocActions } from './bpmn.mjs';
import { checkExpression } from './expressions.mjs';
import { clone, digest } from './values.mjs';
import { AsysLoopCharacteristics, AsysStandardLoopCharacteristics } from './loop.mjs';
import { AsysAdHocFlow } from './ad-hoc-elements.mjs';

export const ENGINE = 'bpmn-elements@17.3.0+asys.2';

export async function prepareEngine(artifact) {
  validateExecutable(artifact);
  const parsed = await readBpmn(artifact.source);
  for (const element of Object.values(parsed.elementsById)) {
    for (const resource of element.resources ?? []) resource.resourceAssignmentExpression ??= {};
  }
  if (!parsed.rootElement.id) parsed.rootElement.id = `Workflow_${artifact.sourceHash.slice(0, 16)}`;
  const context = serializer(parsed, TypeResolver(Elements));
  const definition = JSON.parse(context.serialize());
  const index = indexElements(artifact.document);
  adaptDefinition(definition, index, artifact.bindings);
  return definition;
}

function adaptDefinition(definition, index, bindings) {
  const ids = new Set(index.keys());
  function uniqueId(base) {
    let id = base;
    while (ids.has(id)) id += '_';
    ids.add(id);
    return id;
  }
  // Upstream treats AdHocSubProcess as SubProcess. Inbound flows prevent
  // automatic starts; AsysAdHocFlow turns dependencies into selections (P5).
  for (const scope of definition.activities.filter(a => a.type === 'bpmn:AdHocSubProcess')) {
    const actions = new Set(adHocActions(index.get(scope.id), bindings).map(action => action.id));
    const id = uniqueId(`_asys_${digest(scope.id).slice(0, 32)}`);
    const parent = { id: scope.id, type: scope.type };
    definition.activities.push({ id, type: 'bpmn:ServiceTask', parent,
      behaviour: { asysCoordinator: scope.id } });
    const targets = new Set();
    for (const flow of definition.sequenceFlows.filter(flow => flow.parent.id === scope.id)) {
      // Only selectable work waits for the coordinator. Native flows retain
      // token delivery and discard propagation into gateways and events.
      if (actions.has(flow.targetId)) flow.behaviour.asysAdHoc = scope.id;
      targets.add(flow.targetId);
    }
    for (const child of definition.activities.filter(child => child.parent.id === scope.id)) {
      if (child.id === id || targets.has(child.id) || child.type === 'bpmn:BoundaryEvent'
        || child.behaviour.isForCompensation || child.behaviour.triggeredByEvent) continue;
      definition.sequenceFlows.push({ id: uniqueId(`_asys_flow_${digest([scope.id, child.id]).slice(0, 32)}`),
        type: 'bpmn:SequenceFlow', parent, sourceId: id, targetId: child.id, behaviour: { asysAdHoc: scope.id, asysSynthetic: true } });
    }
  }
  for (const entry of [...definition.activities, ...definition.sequenceFlows]) {
    const behaviour = entry.behaviour;
    // Upstream BpmnIO uses the serializer's single-source projection. Keep
    // association ownership in data.mjs using the preserved BPMN model (P6).
    delete behaviour.ioSpecification;
    delete behaviour.properties;
    // SequenceFlow selects scripts by this language, including when the XML
    // omits the per-expression language and uses our FEEL default (P12).
    if (behaviour.conditionExpression) behaviour.conditionExpression.language = 'feel';
    const loop = behaviour.loopCharacteristics?.behaviour;
    const loopModel = index.get(entry.id)?.loopCharacteristics;
    // Native loops consume collection/elementVariable, not BPMN's
    // loopDataInputRef. data.mjs supplies the referenced input (P11).
    if (loopModel?.loopDataInputRef) {
      loop.collection = `= get value(inputs, ${JSON.stringify(loopModel.loopDataInputRef.$ref)})`;
      loop.elementVariable = 'item';
    }
    // The serializer emits bare expression bodies; the asys expression
    // resolver distinguishes FEEL from literal values using '=' (P11).
    for (const key of ['loopCardinality', 'completionCondition', 'loopCondition']) {
      if (loop?.[key]) loop[key] = `= ${checkExpression(loop[key], `${entry.id} ${key}`)}`;
    }
    // Native standard-loop conditions mean stop; BPMN means continue (P10).
    if (behaviour.loopCharacteristics?.type === 'bpmn:StandardLoopCharacteristics' && loop.loopCondition) {
      loop.loopCondition = `= not(${checkExpression(loop.loopCondition)})`;
    }
    // Language-less conditional events use resolveExpression (P8); explicit
    // FEEL scripts stay intact and register through scripts.register (P8s).
    for (const event of behaviour.eventDefinitions ?? []) {
      if (event.type !== 'bpmn:ConditionalEventDefinition' || event.behaviour.script) continue;
      event.behaviour.expression = `= ${checkExpression(event.behaviour.expression, `${entry.id} condition`)}`;
    }
  }
}

export function executionContext(definition, bindings, Service, Coordinator, enable) {
  const resolve = TypeResolver(Elements, mapper => {
    mapper['bpmn:MultiInstanceLoopCharacteristics'] = AsysLoopCharacteristics;
    mapper['bpmn:StandardLoopCharacteristics'] = AsysStandardLoopCharacteristics;
    mapper['bpmn:SequenceFlow'] = function SequenceFlow(definition, context) {
      return definition.behaviour.asysAdHoc
        ? new AsysAdHocFlow(definition, context, enable) : new Elements.SequenceFlow(definition, context);
    };
  });
  return deserialize(clone(definition), entity => {
    resolve(entity);
    if (entity.behaviour?.asysCoordinator) {
      entity.Behaviour = Elements.ServiceTask;
      entity.behaviour.Service = Coordinator;
    } else if (Object.hasOwn(bindings, entity.id) && entity.type !== 'bpmn:AdHocSubProcess') {
      // A task's BPMN identity is retained. Its execution is a filesystem job,
      // irrespective of whether its worker is a program, agent, or human bridge.
      entity.Behaviour = Elements.ServiceTask;
      entity.behaviour.Service = Service;
    }
  });
}
