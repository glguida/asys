"""Locate and export the portable asys Agent Skill."""
from pathlib import Path
import shutil


def skill(args):
    source = Path(__file__).resolve().parents[2] / 'skills' / 'asys'
    if not (source / 'SKILL.md').is_file():
        raise ValueError(f'asys skill is missing from {source}; reinstall asys')
    if args.destination is None:
        print(source)
        return

    destination = args.destination.expanduser().resolve() / 'asys'
    if destination.exists() or destination.is_symlink():
        raise ValueError(f'{destination} already exists; choose another destination or remove that copy before reinstalling')
    if destination.is_relative_to(source):
        raise ValueError('Choose a destination outside the bundled skill directory')
    shutil.copytree(source, destination)
    print(destination)
