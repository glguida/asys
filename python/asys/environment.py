"""Author environment files and shared skills without changing worker definitions."""
import argparse
import json
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile

from asys_runtime.files import write_text_atomic
from .worker_definitions import (authoring_lock, edit_validated, ensure_environment,
    scoped_path, validate_definition, validate_runtime_text)


def _frontmatter_text(raw):
    value = raw.strip()
    if value.startswith('"'):
        try:
            return json.loads(value)
        except ValueError as error:
            raise ValueError('Invalid quoted skill metadata') from error
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError('Invalid quoted skill metadata')
        return value[1:-1].replace("''", "'")
    value = value.split(' #', 1)[0].rstrip()
    if (not value or value.startswith('#') or value[0] in '[{&*!@`'
            or re.search(r':\s', value)
            or value.lower() in {'null', '~', 'true', 'false'}
            or re.fullmatch(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?', value)):
        raise ValueError('Skill name and description must be YAML text; quote special values')
    return value


def validate_skill_text(text, *, directory_name=None):
    """Validate the portable skill identity and description without importing code."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        raise ValueError('SKILL.md must start with YAML metadata delimited by ---')
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == '---')
    except StopIteration as error:
        raise ValueError('SKILL.md metadata needs a closing ---') from error
    fields = {}
    index = 1
    while index < end:
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith('#') or line[:1].isspace():
            continue
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9_-]*):\s*(.*)', line)
        if not match:
            raise ValueError('Skill metadata must contain named YAML fields')
        key, raw = match.groups()
        if key in fields:
            raise ValueError(f'Duplicate skill metadata: {key}')
        if key not in {'name', 'description'}:
            # Other standard metadata may contain nested maps and sequences.
            # Only the identity and discovery text are interpreted here.
            fields[key] = raw
            continue
        if raw.strip() in {'|', '|-', '|+', '>', '>-', '>+'}:
            pieces = []
            while index < end and (not lines[index].strip() or lines[index][:1].isspace()):
                pieces.append(lines[index].strip())
                index += 1
            value = '\n'.join(pieces) if raw.startswith('|') else ' '.join(pieces)
        else:
            value = _frontmatter_text(raw)
            # A plain or quoted description can continue on indented lines.
            while index < end and lines[index][:1].isspace():
                value += ' ' + lines[index].strip()
                index += 1
        fields[key] = value
    name, description = fields.get('name'), fields.get('description')
    if (not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name)
            or len(name) > 64):
        raise ValueError('Skill name must be 1–64 lowercase letters, numbers and single hyphens')
    if directory_name is not None and name != directory_name:
        raise ValueError('Skill name must match its directory name')
    if (not isinstance(description, str) or not description.strip() or len(description) > 1024
            or '\0' in description or '<' in description or '>' in description):
        raise ValueError('Skill description must be 1–1024 characters without NUL or angle brackets')
    return {'name': name, 'description': description}


def validate_skill_directory(source):
    source = Path(source).expanduser()
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Skill source must be a directory, not a symlink')
    source = source.resolve()
    for path in [source, *source.rglob('*')]:
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f'Skill imports support only regular files and directories: {path.relative_to(source)}')
    metadata = source / 'SKILL.md'
    if not metadata.is_file():
        raise ValueError('Skill directory must contain SKILL.md')
    return source, validate_skill_text(metadata.read_text(encoding='utf-8'), directory_name=source.name)


def add_skill(environment, source):
    source, metadata = validate_skill_directory(source)
    with authoring_lock(environment) as root:
        ensure_environment(root)
        destination = scoped_path(root, f'skills/{metadata["name"]}')
        if destination.exists():
            raise ValueError(f'Skill {metadata["name"]} already exists')
        if destination.is_relative_to(source):
            raise ValueError('Choose an environment outside the source skill directory')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.asys-skill-', dir=destination.parent) as temporary:
            staging = Path(temporary) / metadata['name']
            # Preserve links during copying so a raced-in link is rejected by
            # the second validation, never followed into another directory.
            shutil.copytree(source, staging, symlinks=True)
            validate_skill_directory(staging)
            staging.rename(destination)
    return destination


def import_dockerfile(environment, source):
    text = Path(source).expanduser().read_text(encoding='utf-8')
    if not text.strip() or '\0' in text:
        raise ValueError('Dockerfile must be nonempty text without NUL characters')
    with authoring_lock(environment) as root:
        ensure_environment(root)
        destination = scoped_path(root, 'Dockerfile')
        write_text_atomic(destination, text, mode=0o644)
    return destination


def edit_environment(environment, relative='Dockerfile'):
    # Scope the requested path before creating any environment files.
    scoped_path(environment, relative)
    with authoring_lock(environment) as root:
        ensure_environment(root)
        destination = scoped_path(root, relative, existing=True)
        def validate(text):
            if '\0' in text:
                raise ValueError('Environment text files must not contain NUL')
            if destination == root / 'workers.json':
                validate_runtime_text(text, root)
            elif destination.parent == root / 'workers' and destination.suffix == '.json':
                validate_definition(json.loads(text), root)
            elif destination.name == 'SKILL.md':
                validate_skill_text(text, directory_name=destination.parent.name)
            elif destination == root / 'Dockerfile' and not text.strip():
                raise ValueError('Dockerfile must not be empty')
        edit_validated(destination, validate)
    return destination


def arguments(argv):
    parser = argparse.ArgumentParser(prog='asys-environment', allow_abbrev=False,
        description='Manage the files, dependencies and shared skills of a worker environment.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Examples:\n  asys-environment add-skill ./env/development ./skills/checking\n'
               '  asys-environment dockerfile ./env/development ./Dockerfile\n'
               '  asys-environment edit ./env/development agents/editor/prompt.md\n\n'
               'ENVIRONMENT contains Dockerfile, component.dcomp and workers.json.\n'
               'Use asys-workers add ENVIRONMENT KIND NAME to create named workers.\n'
               'These commands edit source files; they do not start a run.')
    commands = parser.add_subparsers(dest='command', required=True, metavar='COMMAND', title='commands')
    def command(name, summary, detail):
        result = commands.add_parser(name, help=summary, description=detail, allow_abbrev=False)
        result.add_argument('environment', type=Path, metavar='ENVIRONMENT', help='worker environment directory')
        return result
    skill = command('add-skill', 'Import a complete skill directory',
        'Validate SKILL.md and copy the entire skill, including references and assets, into '
        'ENVIRONMENT/skills/NAME. Existing skills are not overwritten. Missing environment files are created.')
    skill.add_argument('source', type=Path, metavar='SKILLDIR', help='directory containing SKILL.md')
    dockerfile = command('dockerfile', 'Replace the environment Dockerfile',
        'Copy FILE to ENVIRONMENT/Dockerfile. Other environment files are preserved. '
        'The Dockerfile must package the programs and tools needed by this environment.')
    dockerfile.add_argument('source', type=Path, metavar='FILE', help='nonempty Dockerfile to import')
    edit = command('edit', 'Edit an existing environment file',
        'Use $VISUAL or $EDITOR to edit a file within ENVIRONMENT. JSON definitions, '
        'workers.json and SKILL.md are validated before replacement; invalid edits keep the original.')
    edit.add_argument('file', nargs='?', default='Dockerfile', metavar='FILE',
        help='path relative to ENVIRONMENT (default: Dockerfile)')
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == 'add-skill':
            print(add_skill(args.environment, args.source))
        elif args.command == 'dockerfile':
            print(import_dockerfile(args.environment, args.source))
        else:
            print(edit_environment(args.environment, args.file))
        return 0
    except (OSError, ValueError) as error:
        print(f'asys-environment: {error}', file=sys.stderr)
        return 1
