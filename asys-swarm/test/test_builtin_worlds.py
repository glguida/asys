"""Reusable visibility rules plus a separately configured measured sample."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from worlds.common import ArtifactWorld, Evaluator
from worlds.samples.route.participant import decision


ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / 'asys-workers/worlds/samples/route'


class NumericEvaluator:
    identity = 'numeric-test-v1'
    def __call__(self, candidate, problem):
        if type(candidate) is not int:
            return {'accepted': False, 'reason': 'An integer is required'}
        return {'accepted': True, 'score': candidate, 'details': {'checked': True}}


class VisibilityTests(unittest.TestCase):
    def make(self, kind='leaderboard', **settings):
        world = ArtifactWorld(kind, NumericEvaluator())
        state = world.initialize(settings, ['agent-a', 'agent-b'], 0)
        return world, state

    def test_worlds_start_empty_without_an_implicit_domain_or_candidate(self):
        for kind in ('leaderboard', 'torus'):
            world, state = self.make(kind)
            self.assertEqual(state['problem'], {})
            self.assertIsNone(state['baseline'])
            self.assertEqual(world.observe(state, 'agent-a')['artifacts'], [])
            self.assertIsNone(world.artifacts(state)['best'])
            self.assertFalse(world.evaluate(state, {'scoreAtMost': 0})['achieved'])

    def test_top_k_zero_means_all_and_positive_means_exact_best_count(self):
        for top_k, expected in ((0, [1, 2, 3]), (1, [1]), (2, [1, 2]), (10, [1, 2, 3])):
            with self.subTest(top_k=top_k):
                world, state = self.make(top_k=top_k)
                for candidate in (3, 1, 2):
                    state = world.step(state, {'agent-a': {'candidate': candidate}})['state']
                self.assertEqual([item['score'] for item in world.observe(state, 'agent-b')['artifacts']], expected)
                self.assertEqual(len(state['artifacts']), 3)

    def test_top_k_rejects_negative_boolean_or_fractional_values(self):
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.make(top_k=value)

    def test_initial_candidate_is_independently_checked_before_members(self):
        with self.assertRaisesRegex(ValueError, 'Initial candidate.*integer'):
            self.make(problem={'initial': {}})
        world, state = self.make(problem={'initial': 100})
        self.assertEqual(world.observe(state, 'agent-a')['baseline']['score'], 100)

    def test_maximization_uses_high_scores_and_explicit_threshold(self):
        world, state = self.make(direction='maximize', top_k=1)
        state = world.step(state, {'agent-a': {'candidate': 3}, 'agent-b': {'candidate': 7}})['state']
        self.assertEqual(world.observe(state, 'agent-a')['artifacts'][0]['score'], 7)
        self.assertTrue(world.evaluate(state, {'scoreAtLeast': 6})['achieved'])

    def test_torus_wraps_both_movement_and_manhattan_visibility(self):
        world, state = self.make('torus', width=5, height=5, radius=1)
        state['positions'] = {'agent-a': {'x': 0, 'y': 0}, 'agent-b': {'x': 4, 'y': 0}}
        state = world.step(state, {'agent-b': {'candidate': 8, 'move': 'west'}})['state']
        self.assertEqual(state['artifacts'][0]['position'], {'x': 4, 'y': 0})
        self.assertEqual(len(world.observe(state, 'agent-a')['artifacts']), 1)
        state = world.step(state, {'agent-a': {'candidate': 9, 'move': 'north'}})['state']
        self.assertEqual(state['positions']['agent-a'], {'x': 0, 'y': 4})
        self.assertEqual([item['agent'] for item in world.observe(state, 'agent-a')['artifacts']], ['agent-a'])

    def test_hidden_artifacts_and_same_round_parents_cannot_be_inherited(self):
        world, state = self.make('torus', width=8, height=8, radius=0)
        state = world.step(state, {'agent-a': {'candidate': 4}})['state']
        hidden = state['artifacts'][0]['id']
        observation = world.observe(state, 'agent-b')
        self.assertEqual(observation['artifacts'], [])
        self.assertNotIn('ranking', observation)
        self.assertNotIn('metrics', observation)
        state = world.step(state, {'agent-b': {'candidate': 3, 'parent': hidden}})['state']
        self.assertFalse(state['feedback']['agent-b']['accepted'])
        world, state = self.make()
        state = world.step(state, {'agent-a': {'candidate': 4},
            'agent-b': {'candidate': 3, 'parent': 'artifact-0001-agent-a'}})['state']
        self.assertFalse(state['feedback']['agent-b']['accepted'])

    def test_local_encounter_allows_declared_cross_agent_reuse(self):
        world, state = self.make('torus', width=4, height=4, radius=1)
        state['positions'] = {'agent-a': {'x': 0, 'y': 0}, 'agent-b': {'x': 2, 'y': 0}}
        state = world.step(state, {'agent-a': {'candidate': 8},
            'agent-b': {'candidate': 'invalid', 'move': 'west'}})['state']
        parent = world.observe(state, 'agent-b')['artifacts'][0]['id']
        state = world.step(state, {'agent-b': {'candidate': 7, 'parent': parent}})['state']
        self.assertEqual(state['counts']['crossAgentReuse'], 1)

    def test_worse_recent_artifact_survives_and_duplicate_source_can_replicate(self):
        world, state = self.make('torus', width=4, height=4, radius=4, visible_artifacts=2)
        for candidate in (1, 2, 9):
            state = world.step(state, {'agent-a': {'candidate': candidate}})['state']
        self.assertEqual([item['score'] for item in world.observe(state, 'agent-b')['artifacts']], [1, 9])
        state = world.step(state, {'agent-b': {'candidate': 1}})['state']
        self.assertEqual(len([item for item in state['artifacts'] if item['score'] == 1]), 2)
        self.assertEqual(len(state['artifacts']), 4)

    def test_capacity_keeps_existing_artifacts_and_rejected_candidates_can_move(self):
        world, state = self.make('torus', width=4, height=4, max_artifacts=1)
        state = world.step(state, {'agent-a': {'candidate': 1}})['state']
        original = deepcopy(state['artifacts'])
        state = world.step(state, {'agent-a': {'candidate': 2, 'move': 'west'}})['state']
        self.assertEqual(state['artifacts'], original)
        self.assertIn('capacity', state['feedback']['agent-a']['reason'])
        self.assertEqual(state['positions']['agent-a']['x'], 3)

    def test_action_order_is_deterministic_and_input_state_is_unchanged(self):
        world, state = self.make('torus')
        original = deepcopy(state)
        a = {'agent-a': {'candidate': 3, 'move': 'east'}, 'agent-b': {'candidate': 5}}
        self.assertEqual(world.step(state, a), world.step(state, dict(reversed(list(a.items())))))
        self.assertEqual(state, original)


class MeasuredSampleTests(unittest.TestCase):
    def evaluator(self):
        return Evaluator([sys.executable, str(SAMPLE / 'evaluate.py')])

    def test_external_evaluator_rejects_missing_visits_and_measures_full_route(self):
        problem = json.loads((SAMPLE / 'problem.json').read_text())
        evaluator = self.evaluator()
        self.assertEqual(evaluator(problem['initial'], problem)['score'], 44)
        self.assertEqual(evaluator({'tour': list(range(8))}, problem)['score'], 16)
        self.assertFalse(evaluator({'tour': list(range(7))}, problem)['accepted'])
        self.assertFalse(evaluator({'tour': [0] * 8}, problem)['accepted'])

    def test_scripted_sample_reaches_measured_target_without_model_calls(self):
        problem = json.loads((SAMPLE / 'problem.json').read_text())
        world = ArtifactWorld('leaderboard', self.evaluator())
        state = world.initialize({'problem': problem}, ['agent-a', 'agent-b'], 0)
        for turn in range(8):
            actions = {agent: decision({'agent': agent, 'turn': turn,
                'observation': world.observe(state, agent)})['actions'][0] for agent in state['agents']}
            state = world.step(state, actions)['state']
            if world.evaluate(state, {'scoreAtMost': 16})['achieved']:
                break
        best = world.artifacts(state)['best']
        self.assertEqual(best['score'], 16)
        self.assertEqual(self.evaluator()(best['candidate'], problem)['score'], 16)

    def test_bundled_component_requires_task_specific_evaluator_setup(self):
        from asys.world_packages import resolve_package
        package = resolve_package(ROOT, 'builtin:leaderboard')
        evaluator = Evaluator([sys.executable, str(package['component'].parent / 'evaluate.py')])
        report = evaluator({}, {})
        self.assertFalse(report['accepted'])
        self.assertIn('Configure this evaluator', report['reason'])


if __name__ == '__main__':
    unittest.main()
