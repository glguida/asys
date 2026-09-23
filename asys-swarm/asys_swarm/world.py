"""The user-authored world is the sole authority for action consequences."""
import importlib.util
from pathlib import Path
import sys

from jsonschema import Draft7Validator

from .config import digest, json_value, package_file
from .limits import ARTIFACT_BYTES, STEP_BYTES, WORLD_BYTES


CALLBACKS = ('initialize', 'observe', 'step', 'evaluate', 'artifacts', 'action_schema')


def local_schema(schema):
    """Schemas may refer to their own definitions, never to network resources."""
    if isinstance(schema, dict):
        if '$ref' in schema and (not isinstance(schema['$ref'], str) or not schema['$ref'].startswith('#')):
            raise ValueError('Action schema references must be local fragments')
        for value in schema.values():
            local_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            local_schema(value)


class World:
    def __init__(self, package, config):
        path = package_file(package, config['world']['module'], 'world.module')
        spec = importlib.util.spec_from_file_location(f'asys_world_{digest(str(path))[:16]}', path)
        self.module = importlib.util.module_from_spec(spec)
        # World files are trusted executable experiment definitions. They run only
        # in the controller; the host validates paths without importing them.
        sys.path.insert(0, str(path.parent))
        try:
            sys.modules[spec.name] = self.module
            spec.loader.exec_module(self.module)
        finally:
            sys.path.remove(str(path.parent))
        for callback in CALLBACKS:
            if not callable(getattr(self.module, callback, None)):
                raise ValueError(f'World must define {callback}()')
        self.schema = json_value(self.module.action_schema(), limit=128 * 1024)
        if not isinstance(self.schema, dict):
            raise ValueError('action_schema() must return a JSON Schema object')
        local_schema(self.schema)
        Draft7Validator.check_schema(self.schema)
        self.validator = Draft7Validator(self.schema)

    def call(self, name, *args):
        # Evaluation/observation cannot accidentally mutate authoritative state.
        copied = []
        for index, arg in enumerate(args):
            if name in {'observe', 'step', 'evaluate', 'artifacts'} and index == 0:
                copied.append(json_value(arg, limit=WORLD_BYTES))
            elif name == 'step' and index == 1:
                copied.append(json_value(arg, limit=STEP_BYTES))
            else:
                copied.append(json_value(arg))
        result = getattr(self.module, name)(*copied)
        if name == 'initialize':
            return json_value(result, limit=WORLD_BYTES)
        if name == 'step':
            result = json_value(result, limit=STEP_BYTES)
            if isinstance(result, dict) and 'state' in result:
                json_value(result['state'], limit=WORLD_BYTES)
            return result
        if name == 'artifacts':
            # Budget the deepest durable embedding, not just the standalone
            # object: indentation must not consume the terminal outbox reserve.
            envelope = {'outbox': [{'data': {'output': {'artifacts': result}}}]}
            return json_value(envelope, limit=ARTIFACT_BYTES)['outbox'][0]['data']['output']['artifacts']
        return json_value(result)

    def evaluation(self, state, objective):
        value = self.call('evaluate', state, objective or {})
        if not isinstance(value, dict) or type(value.get('achieved')) is not bool:
            raise ValueError('evaluate() must return an object with boolean achieved')
        if not isinstance(value.get('metrics'), dict) or not isinstance(value.get('summary'), str):
            raise ValueError('evaluate() must return metrics (object) and summary (text)')
        if objective is None:
            value['achieved'] = None
        return value

    def decision(self, result, *, max_actions, memory_bytes):
        if not isinstance(result, dict) or not isinstance(result.get('actions'), list) or 'memory' not in result:
            raise ValueError('Decision must contain actions and memory')
        if len(result['actions']) > max_actions:
            raise ValueError('Decision exceeds its action limit')
        json_value(result['memory'], limit=memory_bytes, indented=False)
        for action in result['actions']:
            self.validator.validate(action)
        usage = result.get('usage', {})
        if not isinstance(usage, dict):
            raise ValueError('Decision usage must be an object')
        for key in ('input', 'output', 'totalTokens'):
            if key in usage and (type(usage[key]) is not int or usage[key] < 0):
                raise ValueError('Decision token counts must be nonnegative integers')
        return json_value(result)


def package_digest(package):
    """Hash the code/assets actually mounted with this experiment."""
    import hashlib
    root = Path(package).resolve(strict=True)
    hasher = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if '__pycache__' in path.parts or '.git' in path.parts:
            continue
        if path.is_symlink():
            raise ValueError('Experiment package must not contain symlinks')
        if path.is_file():
            hasher.update(path.relative_to(root).as_posix().encode() + b'\0')
            hasher.update(path.read_bytes())
            hasher.update(b'\0')
    return hasher.hexdigest()
