"""Locate and export the portable operating and authoring skills."""
from pathlib import Path
import shutil


def skill(args):
    bundle = Path(__file__).resolve().parents[2] / 'skills'
    names = [args.name] if args.name else ['asys', 'asys-authoring']
    sources = [bundle / name for name in names]
    for source in sources:
        if not (source / 'SKILL.md').is_file():
            raise ValueError(f'asys skill is missing from {source}; reinstall asys')
    if args.destination is None:
        for source in sources:
            print(source)
        return
    parent = args.destination.expanduser().resolve()
    if parent.is_relative_to(bundle):
        raise ValueError('Choose a destination outside the bundled skill directory')
    destinations = [parent / name for name in names]
    for destination in destinations:
        if destination.exists() or destination.is_symlink():
            raise ValueError(f'{destination} already exists; choose another destination or remove that copy before reinstalling')
    for source, destination in zip(sources, destinations):
        shutil.copytree(source, destination)
        print(destination)
