"""Installed built-in agent instructions and names."""
from pathlib import Path

from asys_runtime.files import validate_name


DEFINITIONS = Path(__file__).resolve().parent


def agent_names():
    return tuple(sorted(path.parent.name for path in DEFINITIONS.glob('*/prompt.md')))


def agent_prompt(name):
    validate_name('system agent name', name)
    try:
        return (DEFINITIONS / name / 'prompt.md').read_text(encoding='utf-8')
    except FileNotFoundError as error:
        raise ValueError(f"System agent {name!r} is not installed; available agents: {', '.join(agent_names())}") from error
