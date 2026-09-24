"""Read the component endpoint snapshot supplied by environment preparation."""
from pathlib import Path
import re

from asys_runtime.files import read_json


def world_binding(configuration, environment):
    """Read the immutable host-supplied runtime and renderer selection."""
    reference = configuration['world'].get('package')
    if not reference:
        raise ValueError('A swarm worker requires a world.package component reference')
    path = Path(environment.get('ASYS_WORLD_BINDINGS', '/etc/asys/world-bindings.json'))
    if not path.is_file():
        raise ValueError('World component bindings are missing; prepare the environment before submitting this worker')
    bindings = read_json(path)
    if (not isinstance(bindings, dict) or set(bindings) != {'version', 'packages'}
            or type(bindings['version']) is not int or bindings['version'] != 1
            or not isinstance(bindings['packages'], dict)):
        raise ValueError('Invalid world component binding snapshot')
    binding = bindings['packages'].get(reference)
    if (not isinstance(binding, dict) or 'runtime' not in binding
            or set(binding) - {'runtime', 'view'}):
        raise ValueError(f'World component package is not prepared: {reference}')
    if 'view' in binding and (not isinstance(binding['view'], str)
                             or not re.fullmatch('[0-9a-f]{64}', binding['view'])):
        raise ValueError('World viewer binding must be a SHA-256 generation ID')
    relative = binding['runtime']
    if (not isinstance(relative, str) or '\0' in relative or Path(relative).is_absolute()
            or len(Path(relative).parts) != 2 or Path(relative).parts[0] != 'worlds'
            or '..' in Path(relative).parts or str(Path(relative)) != relative):
        raise ValueError('World runtime binding must be worlds/<binding-id>')
    return binding


def bound_world_root(root, configuration, environment):
    relative = world_binding(configuration, environment)['runtime']
    path = Path(root).resolve()
    for part in Path(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError('World runtime binding must not use symlinks')
    if not path.is_dir():
        raise ValueError('Prepared world component runtime directory is missing')
    return path
