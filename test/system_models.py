import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'tools/asys'


class SystemModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.state = self.directory / 'state'
        self.config = self.state / 'config.json'
        self.env = {**os.environ, 'ASYS_STATE_ROOT': str(self.state)}

    def invoke(self, *args):
        return subprocess.run([sys.executable, str(CLI), 'system-model', *map(str, args)],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def check_success(self, *args):
        result = self.invoke(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_list_reports_unset_defaults_without_creating_state(self):
        self.assertEqual(json.loads(self.check_success('list', '--json')), {'simple': None})
        output = self.check_success('list')
        self.assertIn('simple', output)
        self.assertIn('(not set)', output)
        self.assertFalse(self.state.exists())

    def test_set_and_list_preserve_other_settings_and_model_names(self):
        output = self.check_success('set', 'simple', 'account/first')
        self.assertIn(str(self.config), output)
        self.assertEqual(json.loads(self.config.read_text()), {'system_models': {'simple': 'account/first'}})
        config = json.loads(self.config.read_text())
        config['another_setting'] = {'keep': True}
        self.config.write_text(json.dumps(config))
        self.check_success('set', 'goal', 'account/planner')
        self.check_success('set', 'simple', 'account/second')
        models = {'goal': 'account/planner', 'simple': 'account/second'}
        self.assertEqual(json.loads(self.check_success('list', '--json')), models)
        self.assertEqual(json.loads(self.config.read_text()), {'system_models': models, 'another_setting': {'keep': True}})
        self.assertIn('account/second', self.check_success('list'))

    def test_explicit_root_and_xdg_select_independent_configurations(self):
        explicit = self.directory / 'other setup'
        self.check_success('set', 'simple', 'account/explicit', '--root', explicit)
        self.assertEqual(json.loads(self.check_success('--root', explicit, 'list', '--json')),
                         {'simple': 'account/explicit'})
        self.assertFalse(self.state.exists())
        self.env.pop('ASYS_STATE_ROOT')
        self.env['XDG_STATE_HOME'] = str(self.directory / 'xdg')
        self.check_success('set', 'simple', 'account/xdg')
        self.assertTrue((self.directory / 'xdg/asys/config.json').exists())
        self.assertEqual(json.loads(self.check_success('list', '--json')), {'simple': 'account/xdg'})

    def test_invalid_input_does_not_replace_configuration(self):
        self.check_success('set', 'simple', 'account/model')
        before = self.config.read_bytes()
        for name, model in [('bad/name', 'account/model'), ('', 'account/model'), ('simple', ' '), ('simple', '')]:
            with self.subTest(name=name, model=model):
                self.assertEqual(self.invoke('set', name, model).returncode, 1)
                self.assertEqual(self.config.read_bytes(), before)
        for contents in ['{broken', '[]', '{"system_models": []}']:
            with self.subTest(contents=contents):
                self.config.write_text(contents)
                self.assertEqual(self.invoke('set', 'simple', 'account/new').returncode, 1)
                self.assertEqual(self.invoke('list').returncode, 1)
                self.assertEqual(self.config.read_text(), contents)

    def test_configuration_and_lock_inherit_private_or_shared_permissions(self):
        self.check_success('set', 'simple', 'account/private')
        self.assertEqual(self.state.stat().st_mode & 0o7777, 0o700)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        shared = self.directory / 'shared'
        shared.mkdir()
        shared.chmod(0o2770)
        self.check_success('set', 'simple', 'account/shared', '--root', shared)
        for name in ['config.json', 'config.lock']:
            self.assertEqual((shared / name).stat().st_mode & 0o777, 0o660)
            self.assertEqual((shared / name).stat().st_gid, shared.stat().st_gid)

    def test_concurrent_set_preserves_each_model(self):
        children = []
        for name in ['simple', 'goal', 'review', 'plan']:
            child = subprocess.Popen([sys.executable, str(CLI), 'system-model', 'set', name, f'account/{name}'],
                                     env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            children.append(child)
        for child in children:
            _, error = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, error)
        self.assertEqual(json.loads(self.check_success('list', '--json')),
                         {name: f'account/{name}' for name in ['simple', 'goal', 'review', 'plan']})


if __name__ == '__main__':
    unittest.main()
