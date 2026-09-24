#!/usr/bin/env python3
"""Independently validate a closed delivery route and measure Manhattan length."""
import json
import math
import sys


def evaluate(value):
    problem, candidate = value['problem'], value['candidate']
    cities = problem['cities']
    if (not isinstance(cities, list) or not 3 <= len(cities) <= 64
            or any(not isinstance(point, list) or len(point) != 2
                   or any(type(number) not in (int, float) or not math.isfinite(number) for number in point) for point in cities)):
        raise ValueError('Problem requires 3–64 finite two-dimensional cities')
    if not isinstance(candidate, dict) or set(candidate) != {'tour'}:
        raise ValueError('Candidate must contain only a tour array')
    tour = candidate['tour']
    if (not isinstance(tour, list) or any(type(index) is not int for index in tour)
            or sorted(tour) != list(range(len(cities)))):
        raise ValueError('Route must visit every city exactly once')
    length = sum(abs(cities[left][0] - cities[right][0]) + abs(cities[left][1] - cities[right][1])
                 for left, right in zip(tour, tour[1:] + tour[:1]))
    return {'accepted': True, 'score': length, 'details': {'metric': 'closed Manhattan route length', 'cities': len(cities)}}


if __name__ == '__main__':
    try:
        result = evaluate(json.load(sys.stdin))
    except (KeyError, TypeError, ValueError) as error:
        result = {'accepted': False, 'reason': str(error)}
    json.dump(result, sys.stdout, allow_nan=False)
    sys.stdout.write('\n')
