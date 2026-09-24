"""Shared checked-artifact mechanics for the supplied world programs."""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess

from asys_swarm.world_service import Component


MOVES = {'stay': (0, 0), 'north': (0, -1), 'south': (0, 1), 'east': (1, 0), 'west': (-1, 0)}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode())


class Evaluator:
    """Run an explicitly configured trusted evaluator, separate from members."""
    def __init__(self, command, *, identity=None, timeout=3):
        self.command = command
        if (not isinstance(self.command, list) or not self.command
                or any(not isinstance(item, str) or not item or '\0' in item for item in self.command)):
            raise ValueError('Evaluator command must be a nonempty argument array')
        self.timeout = timeout
        fingerprint = hashlib.sha256(encoded(self.command))
        for item in self.command:
            path = Path(item)
            if path.is_file() and path.stat().st_size <= 8 * 1024 * 1024:
                fingerprint.update(path.read_bytes())
        self.identity = identity or fingerprint.hexdigest()

    def __call__(self, candidate, problem):
        try:
            completed = subprocess.run(self.command, input=encoded({'candidate': candidate, 'problem': problem}),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self.timeout, check=False)
            if completed.returncode:
                raise ValueError(f'Evaluator exited with status {completed.returncode}')
            if len(completed.stdout) > 8192:
                raise ValueError('Evaluator report exceeds 8192 bytes')
            report = json.loads(completed.stdout)
            if not isinstance(report, dict) or type(report.get('accepted')) is not bool:
                raise ValueError('Evaluator requires boolean accepted')
            if report['accepted']:
                score = report.get('score')
                if type(score) not in (int, float) or not math.isfinite(score):
                    raise ValueError('Accepted evaluator report requires a finite numeric score')
                details = report.get('details', {})
                if not isinstance(details, dict) or size(details) > 2048:
                    raise ValueError('Evaluator details must be an object of at most 2048 bytes')
                return {'accepted': True, 'score': score, 'details': details}
            return {'accepted': False, 'reason': str(report.get('reason', 'Rejected by evaluator'))[:512]}
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
            return {'accepted': False, 'reason': str(error)[:512]}


class ArtifactWorld:
    def __init__(self, kind, evaluator):
        if kind not in {'leaderboard', 'torus'}:
            raise ValueError('Unknown world implementation')
        self.kind, self.evaluator = kind, evaluator

    def action_schema(self):
        properties = {'candidate': {}, 'parent': {'type': ['string', 'null']}}
        if self.kind == 'torus':
            properties['move'] = {'enum': list(MOVES)}
        return {'type': 'object', 'properties': properties, 'required': ['candidate'], 'additionalProperties': False}

    def initialize(self, settings, agents, seed):
        allowed = {'problem', 'direction', 'max_artifacts', 'archive_bytes', 'candidate_bytes'}
        allowed |= {'top_k'} if self.kind == 'leaderboard' else {'width', 'height', 'radius', 'visible_artifacts'}
        if not isinstance(settings, dict) or set(settings) - allowed:
            raise ValueError('Unsupported world settings')
        options = {'direction': 'minimize', 'max_artifacts': 256, 'archive_bytes': 128 * 1024,
                   'candidate_bytes': 4096, **deepcopy(settings)}
        if options['direction'] not in {'minimize', 'maximize'}:
            raise ValueError('direction must be minimize or maximize')
        limits = {'max_artifacts': (1, 1000), 'archive_bytes': (4096, 1024 * 1024), 'candidate_bytes': (32, 16384)}
        if self.kind == 'leaderboard':
            options.setdefault('top_k', 0)
            limits['top_k'] = (0, 1000)
        else:
            for key, value in {'width': 8, 'height': 8, 'radius': 1, 'visible_artifacts': 8}.items():
                options.setdefault(key, value)
            limits.update(width=(1, 128), height=(1, 128), radius=(0, 128), visible_artifacts=(1, 32))
        for key, (minimum, maximum) in limits.items():
            if type(options[key]) is not int or not minimum <= options[key] <= maximum:
                raise ValueError(f'{key} must be an integer between {minimum} and {maximum}')
        if self.kind == 'leaderboard' and options['top_k'] == 0 and options['archive_bytes'] > 128 * 1024:
            raise ValueError('top_k=0 requires archive_bytes <= 131072 so all artifacts fit an observation')
        problem = options.pop('problem', {})
        if not isinstance(problem, dict) or size(problem) > 16384:
            raise ValueError('problem must be an object of at most 16384 bytes')
        baseline = None
        if 'initial' in problem:
            if size(problem['initial']) > options['candidate_bytes']:
                raise ValueError('Initial candidate exceeds candidate_bytes')
            report = self.evaluator(problem['initial'], problem)
            if not report['accepted']:
                raise ValueError('Initial candidate failed independent evaluation: ' + report['reason'])
            baseline = {'id': 'baseline', 'agent': None, 'parent': None, 'round': 0,
                        'candidate': problem['initial'], 'score': report['score'], 'details': report['details']}
        state = {'version': 1, 'kind': self.kind, 'round': 0, 'settings': options, 'problem': problem,
            'agents': list(agents), 'baseline': baseline,
            'artifacts': [], 'ranking': [], 'feedback': {}, 'trials': [],
            'counts': {'accepted': 0, 'rejected': 0, 'reused': 0, 'crossAgentReuse': 0},
            'evaluator': self.evaluator.identity}
        if self.kind == 'torus':
            width, height = options['width'], options['height']
            area = width * height
            state['positions'] = {}
            for index, agent in enumerate(agents):
                cell = (seed + index * area // max(1, len(agents))) % area
                state['positions'][agent] = {'x': cell % width, 'y': cell // width}
        return state

    def _rank(self, state, artifacts):
        sign = 1 if state['settings']['direction'] == 'minimize' else -1
        return sorted(artifacts, key=lambda item: (sign * item['score'], item['id']))

    @staticmethod
    def _distance(state, left, right):
        dx, dy = abs(left['x'] - right['x']), abs(left['y'] - right['y'])
        return min(dx, state['settings']['width'] - dx) + min(dy, state['settings']['height'] - dy)

    def _visible(self, state, agent):
        if self.kind == 'leaderboard':
            ranked = self._rank(state, state['artifacts'])
            return ranked[:state['settings']['top_k']] if state['settings']['top_k'] else ranked
        position = state['positions'][agent]
        nearby = [item for item in state['artifacts']
                  if self._distance(state, position, item['position']) <= state['settings']['radius']]
        limit = state['settings']['visible_artifacts']
        # Keep good designs and recent intermediate work in the local slots.
        best = self._rank(state, nearby)[:(limit + 1) // 2]
        selected = {item['id'] for item in best}
        recent = sorted(nearby, key=lambda item: (-item['round'], item['id']))
        return (best + [item for item in recent if item['id'] not in selected])[:limit]

    def observe(self, state, agent):
        if agent not in state['agents']:
            raise ValueError('Unknown participant')
        visible = self._visible(state, agent)
        observation = {'active': True, 'problem': state['problem'], 'baseline': state['baseline'],
            'artifacts': visible, 'feedback': state['feedback'].get(agent),
            'policy': {'kind': self.kind, 'direction': state['settings']['direction'],
                       'description': 'Submit a candidate for independent measurement; parent must be visible.'}}
        if self.kind == 'leaderboard':
            observation['policy']['top_k'] = state['settings']['top_k']
        else:
            position = state['positions'][agent]
            observation['habitat'] = {key: state['settings'][key] for key in ('width', 'height', 'radius')}
            observation['habitat'].update(position=position, moves=list(MOVES), neighbors=[
                {'id': other, **other_position} for other, other_position in sorted(state['positions'].items())
                if other != agent and self._distance(state, position, other_position) <= state['settings']['radius']])
            observation['policy']['description'] += ' Movement wraps; artifacts are published at the starting cell.'
        return deepcopy(observation)

    def step(self, state, actions):
        state = deepcopy(state)
        start = deepcopy(state)
        events, moves = [], {}
        for agent, action in sorted(actions.items()):
            if agent not in state['agents']:
                events.append({'type': 'rejected', 'agent': agent, 'reason': 'Unknown participant'})
                continue
            visible = {item['id']: item for item in self._visible(start, agent)}
            if start['baseline'] is not None:
                visible['baseline'] = start['baseline']
            parent = action.get('parent') if isinstance(action, dict) else None
            move = action.get('move', 'stay') if isinstance(action, dict) else 'stay'
            reason = None
            if not isinstance(action, dict) or 'candidate' not in action:
                reason = 'A candidate is required'
            elif parent is not None and parent not in visible:
                reason = 'Parent was not present in the starting observation'
            elif self.kind == 'torus' and move not in MOVES:
                reason = 'Unknown movement'
            elif size(action['candidate']) > state['settings']['candidate_bytes']:
                reason = 'Candidate exceeds candidate_bytes'
            if self.kind == 'torus' and move in MOVES:
                moves[agent] = move
            report = {'accepted': False, 'reason': reason} if reason else self.evaluator(action['candidate'], state['problem'])
            artifact = None
            if report['accepted']:
                fingerprint = hashlib.sha256(encoded(action['candidate'])).hexdigest()
                position = start.get('positions', {}).get(agent)
                artifact = next((item for item in state['artifacts'] if item['sha256'] == fingerprint
                                 and item.get('position') == position), None)
                if artifact is None:
                    artifact = {'id': f"artifact-{state['round'] + 1:04d}-{agent}", 'agent': agent,
                        'parent': parent, 'round': state['round'] + 1, 'candidate': action['candidate'],
                        'sha256': fingerprint, 'score': report['score'], 'details': report['details']}
                    if position is not None:
                        artifact['position'] = position
                    archive = [*state['artifacts'], artifact]
                    if len(archive) > state['settings']['max_artifacts'] or size(archive) > state['settings']['archive_bytes']:
                        report = {'accepted': False, 'reason': 'Persistent artifact capacity reached; existing work retained'}
                        artifact = None
                    else:
                        state['artifacts'] = archive
            feedback = {'accepted': report['accepted'], 'round': state['round'] + 1,
                        **({'id': artifact['id'], 'score': artifact['score']} if artifact else {'reason': report['reason']})}
            state['counts']['accepted' if report['accepted'] else 'rejected'] += 1
            if report['accepted'] and parent not in (None, 'baseline'):
                state['counts']['reused'] += 1
                state['counts']['crossAgentReuse'] += int(visible[parent]['agent'] != agent)
            state['feedback'][agent] = feedback
            trial = {'agent': agent, 'parent': parent, **feedback}
            state['trials'] = [*state['trials'], trial][-64:]
            events.append({'type': 'artifact.accepted' if report['accepted'] else 'artifact.rejected', **trial})
        for agent, move in sorted(moves.items()):
            dx, dy = MOVES[move]
            before = start['positions'][agent]
            state['positions'][agent] = {'x': (before['x'] + dx) % state['settings']['width'],
                                         'y': (before['y'] + dy) % state['settings']['height']}
        state['round'] += 1
        state['ranking'] = [item['id'] for item in self._rank(state, state['artifacts'])]
        return {'state': state, 'events': events}

    def evaluate(self, state, objective):
        ranked = self._rank(state, ([state['baseline']] if state['baseline'] else []) + state['artifacts'])
        best = ranked[0] if ranked else None
        target = objective.get('scoreAtMost') if state['settings']['direction'] == 'minimize' else objective.get('scoreAtLeast')
        if target is not None and (type(target) not in (int, float) or not math.isfinite(target)):
            raise ValueError('Objective score threshold must be finite')
        achieved = best is not None and target is not None and (best['score'] <= target if state['settings']['direction'] == 'minimize' else best['score'] >= target)
        return {'achieved': achieved, 'metrics': {'baselineScore': state['baseline']['score'] if state['baseline'] else None,
            'bestScore': best['score'] if best else None,
            'artifacts': len(state['artifacts']), **state['counts']},
            'summary': f"Best independently measured score: {best['score']}; {len(state['artifacts'])} stored artifacts." if best else 'No accepted candidate yet.'}

    def artifacts(self, state):
        ranked = self._rank(state, ([state['baseline']] if state['baseline'] else []) + state['artifacts'])
        return {'world': self.kind, 'problem': state['problem']['name'] if 'name' in state['problem'] else 'Custom problem',
            'baseline': state['baseline'], 'best': ranked[0] if ranked else None, 'evaluator': state['evaluator'],
            'ranking': [{key: item[key] for key in ('id', 'agent', 'parent', 'score', 'round')} for item in ranked]}


def serve(kind, command, *, evaluator_id=None, evaluator_timeout=3):
    """Start a component whose implementation chooses its trusted evaluator."""
    if not math.isfinite(evaluator_timeout) or not 0 < evaluator_timeout <= 60:
        raise ValueError('evaluator_timeout must be between 0 and 60 seconds')
    evaluator = Evaluator(command, identity=evaluator_id, timeout=evaluator_timeout)
    rules = ArtifactWorld(kind, evaluator)
    implementation = hashlib.sha256(Path(__file__).read_bytes() + encoded([kind, evaluator.identity])).hexdigest()
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    Component(os.environ.get('ASYS_WORLD_ROOT', '/var/lib/asys-world'), rules,
              identity=f'{kind}-{implementation}').serve(stop=lambda: stopping)
