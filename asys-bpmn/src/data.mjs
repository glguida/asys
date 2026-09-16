import { contextOf, feel } from './expressions.mjs';
import { clone } from './values.mjs';

export function dataValue(id, index, environment, locals = {}) {
  const element = index.get(id);
  if (!element) return undefined;
  if (Object.hasOwn(locals, id)) return locals[id];
  if (element.dataObjectRef) return dataValue(element.dataObjectRef.$ref, index, environment, locals);
  const stored = environment.variables._data ?? {};
  if (Object.hasOwn(stored, id)) return stored[id];
  if (element.name && Object.hasOwn(environment.variables, element.name)) return environment.variables[element.name];
  return Object.hasOwn(environment.variables, id) ? environment.variables[id] : undefined;
}

function names(values, index) {
  const result = { ...values };
  for (const [id, value] of Object.entries(values)) {
    const name = index.get(id)?.name;
    if (name) Object.defineProperty(result, name, { value, enumerable: true, configurable: true });
  }
  return result;
}

function associationValue(association, index, environment, locals, content) {
  const values = Object.fromEntries((association.sourceRef ?? []).map(ref => [ref.$ref, dataValue(ref.$ref, index, environment, locals)]));
  if (association.transformation) return feel(association.transformation.body, { ...contextOf(environment, content), ...names(values, index) });
  return Object.values(values)[0];
}

export function inputValues(model, index, environment, content = {}) {
  const values = Object.fromEntries((model?.ioSpecification?.dataInputs ?? []).map(input => [input.id, dataValue(input.id, index, environment)]));
  for (const association of model?.dataInputAssociations ?? []) {
    values[association.targetRef.$ref] = associationValue(association, index, environment, values, content);
  }
  const ref = model?.loopCharacteristics?.loopDataInputRef?.$ref;
  if (ref && !Object.hasOwn(values, ref)) values[ref] = dataValue(ref, index, environment);
  return names(values, index);
}

export function applyOutput(model, index, environment, output, content) {
  const outputs = model?.ioSpecification?.dataOutputs ?? [];
  const values = Object.fromEntries(outputs.map(item => [item.id,
    output !== null && typeof output === 'object' && Object.hasOwn(output, item.name ?? item.id)
      ? output[item.name ?? item.id] : outputs.length === 1 ? output : null]));
  for (const association of model?.dataOutputAssociations ?? []) {
    storeData(association.targetRef.$ref, associationValue(association, index, environment, values, content));
  }
  const loop = model?.loopCharacteristics;
  if (loop?.loopDataOutputRef && Array.isArray(output)) {
    const key = loop.outputDataItem?.name ?? loop.outputDataItem?.id;
    const collection = output.map(value => key && value && typeof value === 'object' && Object.hasOwn(value, key) ? value[key] : value);
    storeData(loop.loopDataOutputRef.$ref, collection);
  }
  function storeData(id, value) {
    const element = index.get(id);
    if (element?.dataObjectRef) id = element.dataObjectRef.$ref;
    environment.assignVariables({ _data: { ...environment.variables._data, [id]: clone(value ?? null) } });
  }
}
