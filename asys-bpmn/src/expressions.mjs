import { evaluate, parseExpression } from 'feelin';
import { object, publicVariables, variables } from './values.mjs';

export function checkExpression(expression, label = 'expression') {
  const source = expression.trim().replace(/^=/u, '').trim();
  if (!source) throw new Error(`${label} is empty`);
  const tree = parseExpression(source, {});
  tree.iterate({ enter(node) {
    if (node.type.isError) throw new Error(`${label}: invalid FEEL at character ${node.from + 1}`);
  } });
  return source;
}

export function contextOf(environment, content = {}) {
  const values = publicVariables(environment);
  return {
    ...values,
    variables: values,
    output: environment.output,
    item: content.item,
    index: content.index,
    message: content.message,
  };
}

export function feel(source, context, { checkTypes = false } = {}) {
  const result = evaluate(source.trim().replace(/^=/u, '').trim(), context);
  const failure = result.warnings.find(w => ['NO_FUNCTION_FOUND', 'FUNCTION_INVOCATION_FAILURE'].includes(w.type)
    || (checkTypes && w.type === 'INVALID_TYPE'));
  if (failure) {
    const missing = result.warnings.find(w => w.type === 'NO_CONTEXT_ENTRY_FOUND');
    throw new Error([missing?.message, failure.message].filter(Boolean).join('; '));
  }
  return result.value;
}

// These paths describe how an action consumes its input. They are guidance,
// not a guessed schema: a conditional may legitimately read an optional field.
export function messagePaths(expression) {
  const source = expression.trim().replace(/^=/u, '').trim();
  const paths = new Set();
  function rootedAtMessage(node) {
    if (node.name === 'VariableName') return source.slice(node.from, node.to) === 'message';
    return ['PathExpression', 'FilterExpression'].includes(node.name) && rootedAtMessage(node.firstChild);
  }
  parseExpression(source, {}).iterate({ enter(node) {
    if (['PathExpression', 'FilterExpression'].includes(node.name) && rootedAtMessage(node.node)) {
      paths.add(source.slice(node.from, node.to));
      return false;
    }
  } });
  return [...paths].sort();
}

export function valueOf(value, environment, content) {
  return typeof value === 'string' && value.trimStart().startsWith('=')
    ? feel(value, contextOf(environment, content)) : value;
}

export function expressions(context = contextOf) {
  return { resolveExpression(value, { environment, content }) {
    return typeof value === 'string' && value.trimStart().startsWith('=') ? feel(value, context(environment, content)) : value;
  } };
}

// All scripts are FEEL expressions. Script task results are variable updates;
// conditions remain booleans and cannot execute host-language code.
export function scripts(context = contextOf) {
  return {
    register() {},
    getScript(language, owner) {
      const condition = owner.behaviour.conditionExpression;
      const body = condition?.body ?? owner.behaviour.script;
      if (!body) return;
      if (language && !['feel', 'https://www.omg.org/spec/DMN/20191111/FEEL/'].includes(language)) return;
      return { execute(scope, callback) {
        try {
          const result = feel(body, context(scope.environment, scope.content));
          if (condition) {
            if (typeof result !== 'boolean') throw new Error(`Condition ${owner.id} did not return a boolean`);
          } else {
            if (!object(result)) throw new Error(`Script ${owner.id} must return a context of variable updates`);
            scope.environment.assignVariables(variables(result));
          }
          callback(null, result);
        } catch (error) { callback(error); }
      } };
    },
  };
}
