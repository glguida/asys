"""Built-in agents and worker bundles shared by asys launchers.

Agent instructions live alongside this module. Model selection and run state
are supplied by the caller; this package does not depend on a run manager.
"""
from pathlib import Path

from asys_runtime.files import validate_name, write_json, write_text_atomic
from asys_runtime.permissions import mkdir


JOB_TYPE = 'agent'
DEFINITIONS = Path(__file__).resolve().parent


def agent_names():
    return tuple(sorted(path.parent.name for path in DEFINITIONS.glob('*/prompt.md')))


def agent_prompt(name):
    validate_name('system agent name', name)
    try:
        return (DEFINITIONS / name / 'prompt.md').read_text(encoding='utf-8')
    except FileNotFoundError as error:
        raise ValueError(f"System agent {name!r} is not installed; available agents: {', '.join(agent_names())}") from error


def prepare_agent(name, directory, environment, model):
    """Snapshot a system agent into a run's worker bundle for JOB_TYPE jobs."""
    prompt = agent_prompt(name)
    external = Path(directory) / 'external'
    agent = external / 'agents' / name
    mkdir(agent, parents=True)
    write_text_atomic(agent / 'prompt.md', prompt)
    write_json(external / 'workers.json', {
        'version': 1, 'name': environment.name, 'description': f'Asys system agent: {name}',
        'egress': environment.config.get('egress', False),
        'types': {JOB_TYPE: {'command': ['/opt/asys/asys-workers/tools/asys-agent',
                                      '--agent', name, '--model', model]}},
    })
    return external
