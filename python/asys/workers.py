"""Author named worker definitions inside an environment."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys

from asys_runtime.config import validate_types
from asys_runtime.files import read_json, write_json, write_text_atomic
from .worker_definitions import (BUILTINS, KINDS, authoring_lock, bind_definition, builtin_definition,
    definition_path, edit_validated, ensure_environment, environment_config,
    initial_definition, list_definitions, load_definition, scoped_path, validate_definition)


@contextmanager
def _changes(root):
    """Restore changed files on an authoring error; leave existing assets alone."""
    originals = {}
    directories = set()
    def remember(path):
        path = Path(path)
        if path not in originals:
            originals[path] = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
            parent = path.parent
            while parent != root and not parent.exists():
                directories.add(parent)
                parent = parent.parent
    try:
        yield remember
    except BaseException:
        for path, original in reversed(originals.items()):
            if original is None:
                path.unlink(missing_ok=True)
            elif not path.exists() or path.read_bytes() != original[0]:
                write_text_atomic(path, original[0].decode('utf-8'), mode=original[1])
        for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def _swarm_assets(root, name, definition, config, remember):
    member = definition['config']['agents']['type']
    agent = f'{name[:121]}-member'
    if member in config['types'] or definition_path(root, member).exists():
        raise ValueError(f'Swarm member type {member} already exists')
    base = Path(__file__).resolve().parents[2]
    renderer = next((path for path in (base / 'asys-workers/worlds/leaderboard/view.mjs',
                                      base / 'workers/worlds/leaderboard/view.mjs') if path.is_file()), None)
    if renderer is None:
        raise ValueError('Bundled leaderboard renderer is missing; reinstall asys')
    files = {
        f'agents/{agent}/prompt.md': 'Pursue the supplied mission by proposing candidate artifacts.\n'
            'Use the world observation, task specification, and independent evaluator feedback.\n'
            'Do not claim success unless the evaluator has verified the required properties.\n',
        f'programs/{name}-evaluate.py': '"""Replace this stub with task-specific validation and measurement.\n\n'
            'Input: {"candidate": ..., "problem": ...}.\n'
            'Successful output: {"accepted": true, "score": NUMBER, "details": {...}}.\n'
            'Only accept candidates after independently checking their required properties.\n'
            'The configured initial candidate is checked before any member uses inference.\n'
            '"""\nimport json\nimport sys\n\njson.load(sys.stdin)\n'
            'print(json.dumps({"accepted": False, "reason": "Configure this evaluator for the intended task."}))\n',
        definition['config']['world']['view']: renderer.read_text(encoding='utf-8'),
    }
    for relative in files:
        if scoped_path(root, relative).exists():
            raise ValueError(f'Swarm asset already exists: {relative}')
    for relative, text in files.items():
        path = scoped_path(root, relative)
        remember(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, text, mode=0o644)
    config['types'][member] = {'command': ['/opt/asys/asys-workers/tools/asys-swarm-agent', '--agent', agent]}


def arguments(argv):
    parser = argparse.ArgumentParser(prog='asys-workers', description=__doc__)
    parser.add_argument('environment', type=Path, metavar='ENV')
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='List named workers and ordinary program types')
    listing.add_argument('--json', action='store_true', help='Print structured definitions and command bindings')
    add = commands.add_parser('add', help='Create a named worker without replacing an existing type')
    add.add_argument('kind', choices=KINDS, metavar='KIND')
    add.add_argument('name', metavar='NAME')
    add.add_argument('--file', type=Path, help='Import a complete JSON worker definition')
    for name in ('describe', 'edit'):
        command = commands.add_parser(name, help=f'{name.capitalize()} a worker')
        command.add_argument('name', metavar='NAME')
    return parser.parse_args(argv)


def _assets(environment, definition, *, write=True, remember=lambda path: None):
    config = definition['config']
    names = []
    if definition['kind'] == 'agent' and not config.get('system'):
        names.append(config['agent'])
    elif definition['kind'] == 'senate':
        names += [member['agent'] for member in [config['princeps'], *config['senators']] if 'agent' in member]
    paths = []
    for name in names:
        directory = scoped_path(environment, f'agents/{name}')
        if directory.exists() and not directory.is_dir():
            raise ValueError(f'Agent assets must be a directory: {name}')
        prompt = scoped_path(environment, f'agents/{name}/prompt.md')
        if prompt.exists() and not prompt.is_file():
            raise ValueError(f'Agent prompt must be a file: {name}')
        paths.append((directory, prompt))
    for directory, prompt in paths:
        if write and not prompt.exists():
            remember(prompt)
        if write:
            directory.mkdir(parents=True, exist_ok=True)
        if write and not prompt.exists():
            write_text_atomic(prompt, '', mode=0o644)


def add_worker(environment, kind, name, source=None):
    # Validate untrusted input and the destination name before scaffolding.
    definition = validate_definition(read_json(source)) if source is not None else initial_definition(kind, name)
    if definition['kind'] != kind:
        raise ValueError(f'Imported definition kind must be {kind}')
    destination = definition_path(environment, name)
    with authoring_lock(environment) as root:
        ensure_environment(root)
        config = environment_config(root)
        if destination.exists() or name in config['types']:
            raise ValueError(f'Worker {name} already exists')
        config = bind_definition(root, name, definition, write=False)
        bindings = scoped_path(root, 'workers.json')
        with _changes(root) as remember:
            if kind == 'swarm' and source is None:
                _swarm_assets(root, name, definition, config, remember)
            _assets(root, definition, remember=remember)
            remember(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            write_json(destination, definition, mode=0o644)
            remember(bindings)
            write_json(bindings, config, mode=0o644)
    return destination


def worker_records(environment):
    config = environment_config(environment)
    definitions = list_definitions(environment)
    result = {name: {'name': name, 'kind': definitions[name]['kind'] if name in definitions else 'program',
                   **({'definition': definitions[name]} if name in definitions else {}),
                   **({'runtime': config['types'][name]} if name in config['types'] else {})}
            for name in sorted(set(config['types']) | set(definitions))}
    for name in BUILTINS:
        if name not in result:
            definition = builtin_definition(name)
            result[name] = {'name': name, 'kind': definition['kind'], 'definition': definition, 'builtin': True}
    return dict(sorted(result.items()))


def edit_worker(environment, name):
    destination = definition_path(environment, name)
    with authoring_lock(environment) as root:
        config = environment_config(root)
        if destination.exists():
            # Check the existing binding before accepting the edit.
            bind_definition(root, name, write=False)
            def validate(text):
                candidate = validate_definition(json.loads(text), root)
                _assets(root, candidate, write=False)
            with _changes(root) as remember:
                result = edit_validated(destination, validate, before_replace=lambda: remember(destination))
                definition = validate_definition(json.loads(result), root)
                _assets(root, definition, remember=remember)
                remember(scoped_path(root, 'workers.json'))
                bind_definition(root, name, definition)
        else:
            if name not in config['types']:
                raise ValueError(f'Unknown worker: {name}')
            # An ordinary program has no definition file; edit just its command
            # specification and keep every other runtime type unchanged.
            import tempfile
            with tempfile.TemporaryDirectory(prefix='.asys-worker-edit-', dir=root) as temporary:
                part = Path(temporary) / f'{name}.json'
                write_json(part, config['types'][name])
                result = edit_validated(part, lambda text: validate_types({name: json.loads(text)}, directory=root))
            config['types'][name] = json.loads(result)
            write_json(scoped_path(root, 'workers.json'), config)
    return destination if destination.exists() else root / 'workers.json'


def main(argv=None):
    args = arguments(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == 'add':
            print(add_worker(args.environment, args.kind, args.name, args.file))
            if args.kind == 'swarm' and args.file is None:
                print(f'Before running, configure programs/{args.name}-evaluate.py and the world settings.', file=sys.stderr)
        elif args.command == 'edit':
            print(edit_worker(args.environment, args.name))
        else:
            records = worker_records(args.environment)
            if args.command == 'describe':
                if args.name not in records:
                    raise ValueError(f'Unknown worker: {args.name}')
                print(json.dumps(records[args.name], indent=2, ensure_ascii=False))
            elif args.json:
                print(json.dumps(list(records.values()), indent=2, ensure_ascii=False))
            else:
                for name, record in records.items():
                    detail = record.get('definition', {}).get('description', '')
                    kind = record['kind'] + (' (built-in)' if record.get('builtin') else '')
                    print(f'{name}\t{kind}' + (f'\t{detail}' if detail else ''))
        return 0
    except (OSError, ValueError) as error:
        print(f'asys-workers: {error}', file=sys.stderr)
        return 1
