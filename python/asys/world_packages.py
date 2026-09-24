"""Resolve a world component and its bundled DOM viewer without executing code."""
from pathlib import Path
import json
import re



BUILTINS = frozenset({'leaderboard', 'torus', 'terrarium'})
ENDPOINT = re.compile(r'[a-z][a-z0-9-]{0,62}\Z')
TARGET = re.compile(r'(?:@[a-z][a-z0-9_-]{0,62}|[a-z][a-z0-9-]{0,62}\.[a-z][a-z0-9-]{0,62})\Z')


def deployment_settings(value):
    """The package can select ordinary dcomp egress and input links."""
    if not isinstance(value, dict) or set(value) - {'egress', 'links'}:
        raise ValueError('World deployment accepts only egress and links')
    egress, links = value.get('egress', False), value.get('links', {})
    if type(egress) is not bool or not isinstance(links, dict):
        raise ValueError('World deployment requires boolean egress and an input-to-target links object')
    for source, target in links.items():
        if not isinstance(source, str) or not ENDPOINT.fullmatch(source):
            raise ValueError('World deployment link source must be a dcomp input name')
        if not isinstance(target, str) or not TARGET.fullmatch(target):
            raise ValueError('World deployment link target must be COMPONENT.OUTPUT or @GLOBAL')
    return {'egress': egress, 'links': dict(sorted(links.items()))}


def _manifest(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate world manifest field: {key}')
            result[key] = value
        return result
    with path.open('rb') as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError('World manifest exceeds 64 KiB')
    return json.loads(data, object_pairs_hook=unique)


def package_reference(value):
    if not isinstance(value, str) or not value or '\0' in value:
        raise ValueError('world.package must name a world package')
    if value.startswith('builtin:'):
        if value[8:] not in BUILTINS:
            raise ValueError('Unknown built-in world package')
        return value
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or not path.parts or str(path) != value:
        raise ValueError('world.package must be a canonical environment-relative directory')
    return value


def contained(root, relative, *, directory=False):
    if not isinstance(relative, str) or not relative or '\0' in relative:
        raise ValueError('World package paths must be canonical relative paths')
    path = Path(relative)
    if (path.is_absolute() or '..' in path.parts or not path.parts or str(path) != relative):
        raise ValueError('World package paths must be canonical relative paths')
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('World package paths must not use symlinks')
    if not (current.is_dir() if directory else current.is_file()):
        raise ValueError(f'World package {"directory" if directory else "file"} is missing: {relative}')
    return current


def builtin_root(name):
    base = Path(__file__).resolve().parents[2]
    for root in (base / 'asys-workers/worlds', base / 'workers/worlds'):
        if (root / name / 'world.json').is_file():
            return root / name
    raise ValueError(f'Bundled world package {name} is missing; reinstall asys')


def resolve_package(environment, reference):
    """Return root, manifest, component, and view; paths are absolute Paths.

    The host builds the component and snapshots its static viewer. Implementations
    run only in the component; resolving a package never imports its Python/JS.
    """
    reference = package_reference(reference)
    root = (builtin_root(reference[8:]) if reference.startswith('builtin:') else
            contained(Path(environment).expanduser().resolve(), reference, directory=True))
    manifest = _manifest(contained(root, 'world.json'))
    if (not isinstance(manifest, dict) or set(manifest) - {'version', 'component', 'view', 'deployment'}
            or not {'version', 'component', 'view'} <= set(manifest)
            or type(manifest['version']) is not int or manifest['version'] != 1):
        raise ValueError('world.json requires version:1, component, view, and optional deployment')
    if 'deployment' in manifest:
        deployment_settings(manifest['deployment'])
    component = contained(root, manifest['component'])
    view = contained(root, manifest['view'])
    if view.suffix != '.mjs':
        raise ValueError('World viewer entry must be an ES module (.mjs)')
    if component.parent == root:
        if view.parent == root:
            raise ValueError('A root-level world component requires a dedicated viewer subdirectory')
    elif view.is_relative_to(component.parent):
        raise ValueError('World viewer must be outside the component implementation directory')
    # A viewer may import sibling assets. Validate the complete copied package
    # so neither build context nor static snapshots can follow an escaping link.
    for path in root.rglob('*'):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('World package must contain only regular files and directories')
    return {'root': root, 'manifest': manifest, 'component': component, 'view': view}
