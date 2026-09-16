import serializer, { deserialize, TypeResolver } from 'moddle-context-serializer';
import * as Elements from 'bpmn-elements';
import { readBpmn } from './xml.mjs';
import { validateExecutable, indexElements } from './bpmn.mjs';
import { checkExpression } from './expressions.mjs';
import { clone, digest } from './values.mjs';

export const ENGINE = 'bpmn-elements@17.3.0';

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
  for (const scope of definition.activities.filter(a => a.type === 'bpmn:AdHocSubProcess')) {
    let id = `_asys_${digest(scope.id).slice(0, 32)}`;
    while (index.has(id)) id += '_';
    definition.activities.push({ id, type: 'bpmn:ServiceTask', parent: { id: scope.id, type: scope.type },
      behaviour: { asysCoordinator: scope.id } });
  }
  for (const entry of [...definition.activities, ...definition.sequenceFlows]) {
    const behaviour = entry.behaviour;
    // Use the preserved BPMN associations rather than the serializer's
    // single-source I/O projection.
    delete behaviour.ioSpecification;
    delete behaviour.properties;
    if (behaviour.conditionExpression) behaviour.conditionExpression.language = 'feel';
    const loop = behaviour.loopCharacteristics?.behaviour;
    const loopModel = index.get(entry.id)?.loopCharacteristics;
    if (loopModel?.loopDataInputRef) {
      loop.collection = `= get value(inputs, ${JSON.stringify(loopModel.loopDataInputRef.$ref)})`;
      loop.elementVariable = 'item';
    }
    for (const key of ['loopCardinality', 'completionCondition', 'loopCondition']) {
      if (loop?.[key]) loop[key] = `= ${checkExpression(loop[key], `${entry.id} ${key}`)}`;
    }
    if (behaviour.loopCharacteristics?.type === 'bpmn:StandardLoopCharacteristics' && loop.loopCondition) {
      loop.loopCondition = `= not(${checkExpression(loop.loopCondition)})`;
    }
    for (const event of behaviour.eventDefinitions ?? []) {
      if (event.type !== 'bpmn:ConditionalEventDefinition') continue;
      event.behaviour.expression = `= ${checkExpression(event.behaviour.expression ?? event.behaviour.condition?.body ?? '', `${entry.id} condition`)}`;
      delete event.behaviour.script;
    }
  }
  return definition;
}

export function executionContext(definition, bindings, Service, Coordinator, enable) {
  const resolve = TypeResolver(Elements);
  return deserialize(clone(definition), entity => {
    resolve(entity);
    const loop = entity.behaviour?.loopCharacteristics;
    if (loop) {
      const NativeLoop = loop.Behaviour;
      loop.Behaviour = function(activity, definition) {
        const instance = new NativeLoop(activity, definition);
        const execute = instance.execute;
        instance.execute = function(message) {
          const behaviour = definition.behaviour;
          const cardinality = behaviour.loopCardinality ?? behaviour.loopMaximum;
          if (cardinality !== undefined) {
            const value = activity.environment.resolveExpression(cardinality, message);
            if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error(`${activity.id}: loop cardinality must be a nonnegative integer`);
          }
          if (behaviour.collection && !Array.isArray(activity.environment.resolveExpression(behaviour.collection, message))) throw new Error(`${activity.id}: multi-instance input must be a collection`);
          if (definition.type === 'bpmn:StandardLoopCharacteristics' && behaviour.testBefore && behaviour.loopCondition && !message.fields.redelivered
            && activity.environment.resolveExpression(behaviour.loopCondition, message)) {
            activity.broker.publish('execution', 'execute.completed', { ...message.content, output: [] });
            return;
          }
          return execute.call(this, message);
        };
        return instance;
      };
    }
    if (entity.behaviour?.asysCoordinator) {
      entity.Behaviour = Elements.ServiceTask;
      entity.behaviour.Service = Coordinator;
    } else if (Object.hasOwn(bindings, entity.id) && entity.type !== 'bpmn:AdHocSubProcess') {
      // A task's BPMN identity is retained. Its execution is a filesystem job,
      // irrespective of whether its worker is a program, agent, or human bridge.
      entity.Behaviour = Elements.ServiceTask;
      entity.behaviour.Service = Service;
    }
    if (entity.parent?.type === 'bpmn:AdHocSubProcess' && !entity.behaviour?.asysCoordinator) {
      const Behaviour = entity.Behaviour;
      entity.Behaviour = function(activity, context) {
        const instance = new Behaviour(activity, context);
        // Selection belongs to the coordinator. Sequence flows, boundaries,
        // subprocesses and recovery continue using the engine's activities.
        Object.defineProperty(instance, 'isStart', { value: false });
        if (Object.hasOwn(bindings, activity.id) || ['bpmn:SubProcess', 'bpmn:CallActivity', 'bpmn:Transaction'].includes(activity.type)) {
          const run = instance.run;
          instance.run = function(content = {}) {
            if (content.asysSelected) return run.call(this, content);
            enable(this, content);
          };
        }
        return instance;
      };
    }
  });
}
