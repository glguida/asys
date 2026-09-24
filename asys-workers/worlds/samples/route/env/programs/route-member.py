#!/usr/bin/env python3
"""Deterministic route-search member for execution tests; uses no model."""
import hashlib
import json
import os
from pathlib import Path


def decision(value):
    observation = value['observation']
    cities = observation['problem']['cities']
    available = [observation['baseline'], *observation['artifacts']]
    parent = min(available, key=lambda item: (item['score'], -item['round'], item['id']))
    tour = parent['candidate']['tour']
    def length(candidate):
        return sum(sum(abs(cities[a][axis] - cities[b][axis]) for axis in (0, 1))
                   for a, b in zip(candidate, candidate[1:] + candidate[:1]))
    alternatives = []
    for left in range(len(tour) - 1):
        for right in range(left + 1, len(tour)):
            changed = tour[:left] + list(reversed(tour[left:right + 1])) + tour[right + 1:]
            alternatives.append((length(changed), changed))
    alternatives.sort()
    minimum = alternatives[0][0]
    if minimum >= parent['score']:
        # A second ordinary heuristic supplies a new branch when reversals
        # stop improving; the trusted evaluator still measures the result.
        start = int(hashlib.sha256(value['agent'].encode()).hexdigest()[:8], 16) % len(cities)
        greedy, remaining = [start], set(range(len(cities))) - {start}
        while remaining:
            last = greedy[-1]
            next_city = min(remaining, key=lambda index: (
                sum(abs(cities[last][axis] - cities[index][axis]) for axis in (0, 1)), index))
            greedy.append(next_city)
            remaining.remove(next_city)
        alternatives.append((length(greedy), greedy))
        alternatives.sort()
        minimum = alternatives[0][0]
    best = [candidate for score, candidate in alternatives if score == minimum]
    salt = int(hashlib.sha256(f"{value['agent']}:{value['turn']}".encode()).hexdigest()[:8], 16)
    candidate = best[salt % len(best)] if minimum <= parent['score'] else tour
    action = {'candidate': {'tour': candidate}, 'parent': parent['id']}
    if 'habitat' in observation:
        action['move'] = ('east', 'south', 'west', 'north')[(salt + value['turn']) % 4]
    return {'actions': [action], 'memory': {'parent': parent['id']}, 'usage': {'input': 0, 'output': 0, 'totalTokens': 0}}


if __name__ == '__main__':
    value = json.loads(Path(os.environ['ASYS_INPUT']).read_text())
    Path(os.environ['ASYS_RESULT']).write_text(json.dumps(decision(value)) + '\n')
