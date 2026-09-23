"""Validate data-only swarm configuration and Runtime world channel selection."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from asys_runtime.files import read_json, validate_name


LIMITS = {
    'turns': (100, 1, 10000),
    'concurrency': (4, 1, 64),
    'decisions': (1000, 1, 1000000),
    'seconds': (600, 0.1, 86400),
    'jobSeconds': (60, 0.1, 3600),
    'actions': (4, 1, 16),
    'memoryBytes': (4096, 4, 65536),
    'outputTokens': (2048, 128, 16384),
    'tickSeconds': (0.05, 0, 5),
}
INTEGER_LIMITS = {'turns', 'concurrency', 'decisions', 'actions', 'memoryBytes', 'outputTokens'}


def json_value(value, *, limit=256 * 1024, indented=True):
    """Round-trip JSON; forbid non-finite numbers and oversized records."""
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if len(text.encode('utf-8')) > limit:
        raise ValueError(f'JSON value exceeds {limit} bytes')
    # Runtime files use indented JSON. Bound that representation as well so
    # deeply nested but compact values cannot overflow durable records.
    if indented and len(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')) > limit:
        raise ValueError(f'Indented JSON value exceeds {limit} bytes')
    return json.loads(text)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def object_fields(value, name, allowed):
    if not isinstance(value, dict):
        raise ValueError(f'{name} must be an object')
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f'Unknown {name} field(s): {", ".join(sorted(unknown))}')


def package_file(package, value, label):
    if not isinstance(value, str) or not value or '\0' in value:
        raise ValueError(f'{label} must be a relative file path')
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError(f'{label} must remain inside the experiment package')
    root = Path(package).resolve(strict=True)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f'{label} must not use symlinks')
    path = current.resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f'{label} must be a file inside the experiment package')
    return path


def validate_config(value, package=None, *, name='swarm'):
    value = deepcopy(json_value(value, limit=128 * 1024))
    object_fields(value, 'configuration', {'version', 'name', 'mission', 'world', 'objective',
                                         'agents', 'limits', 'seed', 'view'})
    if type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('Swarm configuration version must be 1')
    value.setdefault('name', name)
    for field in ('name', 'mission'):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f'{field} must be nonempty text')
        if len(value[field]) > (256 if field == 'name' else 32000):
            raise ValueError(f'{field} is too long')
    world = value.get('world')
    object_fields(world, 'world', {'channel', 'settings', 'timeoutSeconds'})
    world.setdefault('channel', 'world')
    validate_name('world channel', world['channel'])
    if world['channel'] == 'swarm':
        raise ValueError('The world channel must differ from the swarm control channel')
    world.setdefault('timeoutSeconds', 30)
    timeout = world['timeoutSeconds']
    if type(timeout) not in (int, float) or not 0.01 <= timeout <= 3600:
        raise ValueError('world.timeoutSeconds must be between 0.01 and 3600')
    world.setdefault('settings', {})
    if not isinstance(world['settings'], dict):
        raise ValueError('world.settings must be an object')
    if value.get('objective') is not None and not isinstance(value['objective'], dict):
        raise ValueError('objective must be an object or null (exploration mode)')
    value.setdefault('objective', None)
    agents = value.setdefault('agents', {})
    object_fields(agents, 'agents', {'count', 'type'})
    agents.setdefault('count', 8)
    agents.setdefault('type', 'swarm-step')
    if type(agents['count']) is not int or not 1 <= agents['count'] <= 256:
        raise ValueError('agents.count must be an integer between 1 and 256')
    validate_name('agent job type', agents['type'])
    limits = value.setdefault('limits', {})
    object_fields(limits, 'limits', LIMITS)
    for key, (default, lower, upper) in LIMITS.items():
        limits.setdefault(key, default)
        actual = limits[key]
        numeric = type(actual) is int if key in INTEGER_LIMITS else type(actual) in (int, float)
        if not numeric or not lower <= actual <= upper:
            raise ValueError(f'limits.{key} must be {"an integer" if key in INTEGER_LIMITS else "a number"} between {lower} and {upper}')
    value.setdefault('seed', 0)
    if type(value['seed']) is not int or not 0 <= value['seed'] <= 2**32 - 1:
        raise ValueError('seed must be an integer between 0 and 4294967295')
    if value.get('view') is not None:
        if package is None:
            view = value['view']
            if (not isinstance(view, str) or not view or '\0' in view
                    or Path(view).is_absolute() or '..' in Path(view).parts):
                raise ValueError('view must be a relative package file path')
        else:
            package_file(package, value['view'], 'view')
    return value


def load_config(path):
    path = Path(path).expanduser().resolve(strict=True)
    return validate_config(read_json(path), path.parent, name=path.stem)
