#!/usr/bin/env python3
"""Recheck shared swarm artifacts with the independently verified checker."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess


def compare(workspace):
    spec = importlib.util.spec_from_file_location('route', workspace / 'route.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    problem = json.loads((workspace / 'problem.json').read_text())
    rows, inputs = [], ['problem.json', 'route.py', 'test_route.py']
    for path in sorted(workspace.glob('swarm-runs/*/artifacts.json')):
        artifacts = json.loads(path.read_text())
        best = artifacts['best']
        tour = best['candidate']['tour']
        score = module.route_length(problem['cities'], tour)
        assert sorted(tour) == list(range(len(problem['cities']))), 'Invalid saved tour'
        assert score == best['score'], 'Checker and world measurements disagree'
        assert score >= 16, 'Claimed score contradicts the geometric lower bound'
        inputs.append(str(path.relative_to(workspace)))
        rows.append({'world': artifacts['world'], 'job': path.parent.name, 'tour': tour,
                     'worldScore': best['score'], 'checkedScore': score, 'achieved': score <= 16})
    assert len(rows) == 2 and {row['world'] for row in rows} == {'leaderboard', 'torus'}, 'Both swarm worlds must supply evidence'
    checks = []
    extra = workspace / 'revision-checks.json'
    if extra.exists():
        inputs.append(extra.name)
        for case in json.loads(extra.read_text())['cases']:
            actual = module.route_length(case['cities'], case['tour'])
            assert actual == case['expected'], f'Revision check failed: {case}'
            checks.append({**case, 'actual': actual, 'passed': True})
    tests = subprocess.run(['python3', '-m', 'unittest', '-v'], cwd=workspace, capture_output=True, text=True)
    assert tests.returncode == 0, tests.stdout + tests.stderr
    record = {'sharedWorkspace': str(workspace), 'inputFiles': inputs, 'searches': rows,
              'revisionChecks': checks, 'tests': {'exitCode': tests.returncode, 'output': tests.stdout + tests.stderr}}
    (workspace / 'comparison.json').write_text(json.dumps(record, indent=2) + '\n')
    lines = ['# Measured delivery-route evidence', '', 'The program read both swarm artifacts and the goal-produced route checker from the same workspace.', '']
    lines += [f"- {row['world']}: world score {row['worldScore']}; independently checked score {row['checkedScore']}; tour {row['tour']}." for row in rows]
    lines += ['', f'Additional human revision checks: {len(checks)}.', '', '```text', tests.stdout + tests.stderr, '```']
    (workspace / 'evidence.md').write_text('\n'.join(lines) + '\n')
    return {'final': f"Checked both shared swarm results; scores {[row['checkedScore'] for row in rows]}. Tests passed. {len(checks)} additional revision checks passed.", 'exception': None, **record}


if __name__ == '__main__':
    result = compare(Path.cwd())
    Path(os.environ['ASYS_RESULT']).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
