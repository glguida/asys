"""Settings for system agents in the selected asys setup."""
import json
from pathlib import Path

from asys_runtime.files import file_lock, read_json, validate_name, write_json
from asys_runtime.permissions import mkdir
from .state import state_root
from .system_agents import agent_names


def config_path(root=None):
    return (Path(root) if root is not None else state_root()).expanduser().resolve() / 'config.json'


def read_config(path):
    try:
        config = read_json(path)
    except FileNotFoundError:
        if path.is_symlink():
            raise
        return {}
    except ValueError as error:
        raise ValueError(f'Cannot read {path}: {error}') from error
    if not isinstance(config, dict):
        raise ValueError(f'{path}: configuration must be a JSON object')
    if not isinstance(config.get('system_models', {}), dict):
        raise ValueError(f'{path}: system_models must be a JSON object')
    return config


def model_name(value, label):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError(f'{label} must be nonempty text')
    return value


def system_model(name, override=None):
    if override is not None:
        return model_name(override, '--model')

    path = config_path()
    model = read_config(path).get('system_models', {}).get(name)
    if model is not None:
        return model_name(model, f'{path}: system_models.{name}')

    raise ValueError(
        f"No model configured for system agent '{name}'.\n"
        f'List available models with: asys-inference models\n'
        f'Set the default with: asys system-model set {name} MODEL\n'
        f'Or pass --model MODEL for this run.\n'
        f'Configuration: {path}'
    )


def system_models():
    path = config_path()
    models = read_config(path).get('system_models', {})
    for name, model in models.items():
        validate_name(f'{path}: system model name', name)
        if model is not None:
            model_name(model, f'{path}: system_models.{name}')
    return models


def set_system_model(args):
    path = config_path(args.root)
    name = validate_name('system model name', args.name)
    model = model_name(args.model, 'model')
    mkdir(path.parent, parents=True, exist_ok=True)
    with file_lock(path.with_suffix('.lock'), blocking=True):
        config = read_config(path)
        config.setdefault('system_models', {})[name] = model
        write_json(path, config)
    print(f'{name}: {model}\nSaved in {path}')


def list_system_models(args):
    path = config_path(args.root)
    models = read_config(path).get('system_models', {})
    selected = {}
    for name in sorted(set(agent_names()) | models.keys()):
        validate_name(f'{path}: system model name', name)
        model = models.get(name)
        selected[name] = model_name(model, f'{path}: system_models.{name}') if model is not None else None
    if args.json:
        print(json.dumps(selected, indent=2))
    else:
        width = max(len('SYSTEM-MODEL'), *(len(name) for name in selected))
        print(f'{"SYSTEM-MODEL":<{width}}  MODEL')
        for name, model in selected.items():
            print(f'{name:<{width}}  {model if model is not None else "(not set)"}')
