import { MultiInstanceLoopCharacteristics, RunError } from 'bpmn-elements';

export class AsysLoopCharacteristics extends MultiInstanceLoopCharacteristics {
  #activity;
  #definition;

  constructor(activity, definition) {
    super(activity, definition);
    this.#activity = activity;
    this.#definition = definition;
  }
  execute(message) {
    const activity = this.#activity;
    const definition = this.#definition;
    const behaviour = definition.behaviour;
    const cardinality = behaviour.loopCardinality ?? behaviour.loopMaximum;
    if (cardinality !== undefined) {
      const value = activity.environment.resolveExpression(cardinality, message);
      if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
        throw new RunError(`${activity.id}: loop cardinality must be a nonnegative integer`, message);
      }
    }
    if (behaviour.collection && !Array.isArray(activity.environment.resolveExpression(behaviour.collection, message))) {
      throw new RunError(`${activity.id}: multi-instance input must be a collection`, message);
    }
    // Native SequentialLoop leaves an initially stopped testBefore loop open.
    // Complete it here, but let native recovery handle a redelivered execution.
    // Dependency contracts P2-P4 pin both the defect and completion/replay format.
    if (definition.type === 'bpmn:StandardLoopCharacteristics' && behaviour.testBefore && behaviour.loopCondition
      && !message.fields.redelivered && activity.environment.resolveExpression(behaviour.loopCondition, message)) {
      activity.broker.publish('execution', 'execute.completed', { ...message.content, output: [] });
      return;
    }
    return super.execute(message);
  }
}

export function AsysStandardLoopCharacteristics(activity, definition) {
  return new AsysLoopCharacteristics(activity, { ...definition, behaviour: { ...definition.behaviour, isSequential: true } });
}
