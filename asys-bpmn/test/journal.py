"""Exercise the progress formatter used by the BPMN launcher."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT.parent / 'python')]
from asys_bpmn.journal import WorkflowHistory, agent_notices
from asys_bpmn.runs import Runs, component_names, workflow_name


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class Progress(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='asys-bpmn-progress-')
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name)
        self.job_id = 'wf-0123456789abcdef'
        self.job = self.run / 'runtime/environments/kicad/jobs' / self.job_id
        self.directory = self.run / 'jobs' / self.job_id
        self.directory.mkdir(parents=True)
        (self.run / 'workspace').mkdir()
        self.record = {'id': 'test-run', 'name': 'pcb-engineer'}
        write_json(self.job / 'request.json', {'type': 'agent', 'directory': '../../../jobs/' + self.job_id,
            'workspace': '../../../workspace', 'metadata': {'name': 'design_module', 'activity_id': 'design_module',
                'bpmn': {'name': 'Design one module'}}})
        write_json(self.job / 'state.json', {'id': self.job_id, 'status': 'failed',
            'error': 'Program exited with status 1', 'started_at': '2026-09-14T10:00:24Z'})
        (self.directory / 'stderr.log').write_text('Agent prompt must be a nonempty string\n')
        (self.directory / 'stdout.log').write_text('{"type":"agent.tool_started","name":"bash"}\n')
        self.observer = Runs(self.run)
        self.history = WorkflowHistory(self.run)

    def events(self):
        entries = [
            ('run.created', '', '10:00:00', {}),
            ('activity.start', 'architecture', '10:00:01', {'name': 'Plan architecture', 'executionId': 'architecture_1'}),
            ('job.created', 'architecture', '10:00:01', {'type': 'agent'}),
            ('activity.end', 'architecture', '10:00:11', {'name': 'Plan architecture', 'executionId': 'architecture_1'}),
            ('activity.start', 'module_design', '10:00:12', {'name': 'Design modules', 'executionId': 'modules_1'}),
            ('activity.start', '_asys_internal', '10:00:12', {'internal': True, 'executionId': 'coordinator_1'}),
            ('activity.start', 'design_module', '10:00:13', {'name': 'Design one module', 'executionId': 'design_1'}),
            ('activity.end', 'design_module', '10:00:23', {'name': 'Design one module', 'executionId': 'design_1'}),
            ('activity.start', 'design_module', '10:00:24', {'name': 'Design one module', 'executionId': 'design_2'}),
            ('job.failed', 'design_module', '10:00:25', {'jobId': self.job_id, 'executionId': 'design_2', 'message': 'Program exited with status 1'}),
            ('run.failed', '', '10:00:25', {'message': 'Program exited with status 1'}),
        ]
        return [{'sequence': i + 1, 'runId': self.record['id'], 'type': kind, 'activityId': activity,
                 'time': f'2026-09-14T{when}Z', 'dataJson': json.dumps(data)}
                for i, (kind, activity, when, data) in enumerate(entries)]

    def consume(self, events):
        self.record['jobs'] = self.observer.jobs(self.run)
        return '\n'.join(line for event in events if (line := self.history.consume(event, self.record)))

    def notices(self):
        return '\n'.join(line for _, line in agent_notices(self.observer.progress.events(self.observer.jobs(self.run))))

    def mirror(self, events):
        with (self.run / 'events.jsonl').open('a') as output:
            for event in events:
                output.write(json.dumps(event) + '\n')

    def publish(self, event):
        write_json(self.run / 'runtime/channels/workflow/out' / f"{event['sequence']:09}.json",
            {'sequence': event['sequence'], 'type': event['type'], 'time': event['time'], 'data': {
                'store': event['sequence'], 'runId': self.record['id'], 'activityId': event['activityId'],
                'time': event['time'], 'data': json.loads(event['dataJson'])}})

    def test_stage_order_durations_iterations_and_failure_details(self):
        output = self.consume(self.events())
        self.assertRegex(output, r'10:00:01Z\s+STARTED\s+Plan architecture \(architecture\)')
        self.assertRegex(output, r'10:00:11Z\s+FINISHED\s+Plan architecture \(architecture\).*10s')
        self.assertEqual(output.count('STARTED   Design one module'), 2)
        self.assertRegex(output, r'FAILED\s+Design one module.*#2.*Agent prompt must be a nonempty string')
        self.assertRegex(output, r'RUN FAILED\s+pcb-engineer.*Agent prompt must be a nonempty string')
        self.assertNotRegex(output, r'agent.tool|_asys_internal|job.created|stdout')
        self.assertLess(output.index('FINISHED'), output.index('STARTED   Design modules'))
        discarded = {'sequence': 12, 'type': 'activity.discard', 'activityId': 'unused', 'data': {}}
        self.assertIsNone(self.history.consume(discarded, self.record))

    def test_protobuf_string_sequences_are_replayed_once(self):
        events = [{**event, 'sequence': str(event['sequence'])} for event in self.events()]
        output = self.consume(events)
        self.assertIn('FINISHED  Plan architecture', output)
        self.assertEqual(self.consume(events), '')

    def test_provider_exhaustion_and_retries_keep_workflow_labels(self):
        write_json(self.job / 'state.json', {'id': self.job_id, 'status': 'running'})
        with (self.directory / 'stdout.log').open('a') as output:
            output.write(json.dumps({'type': 'agent.provider_exhausted', 'time': '2026-09-14T10:01:40Z',
                                     'retryAt': '2026-09-14T11:00:00Z', 'delayMs': 3500000}) + '\n')
        output = self.notices()
        self.assertRegex(output, r'EXHAUSTED\s+Design one module.*retry at 2026-09-14T11:00:00Z')
        self.assertNotIn('tool_started', output)
        with (self.directory / 'stdout.log').open('a') as output:
            output.write(json.dumps({'type': 'agent.provider_retrying', 'time': '2026-09-14T11:00:00Z'}) + '\n')
        output = self.notices()
        self.assertRegex(output, r'RETRYING\s+Design one module')
        self.assertEqual(output.count('EXHAUSTED'), 1)

    def test_channel_replay_deduplicates_the_mirror_without_acknowledging(self):
        events = self.events()[:3]
        self.mirror(events[:2])
        cursor = self.run / 'runtime/channels/workflow/out/cursor.json'
        write_json(cursor, {'version': 1, 'after': 2})
        for event in events[:2]:
            self.publish(event)
        self.history.read(self.record)
        self.assertEqual(self.history.after, 2)
        for event in events[2:]:
            self.publish(event)
        self.history.read(self.record)
        self.assertEqual(self.history.after, 3)
        self.assertRegex(self.consume([self.events()[3]]), r'FINISHED\s+Plan architecture.*10s')
        self.publish({'sequence': 5, 'type': 'activity.wait', 'activityId': 'approval',
            'time': '2026-09-14T10:00:30Z', 'dataJson': json.dumps({
                'name': 'Approve design', 'activityType': 'bpmn:ReceiveTask', 'executionId': 'approval_1'})})
        self.history.read(self.record)
        self.assertRegex(self.consume([{'sequence': 6, 'type': 'activity.end', 'activityId': 'approval',
            'time': '2026-09-14T10:00:31Z', 'data': {'executionId': 'approval_1'}}]),
            r'FINISHED\s+Approve design \(approval\)')
        self.mirror(events[2:])
        self.history.read(self.record)
        output = self.consume([{'sequence': 7, 'type': 'activity.start', 'activityId': 'architecture',
            'time': '2026-09-14T10:00:32Z', 'data': {'name': 'Plan architecture', 'executionId': 'architecture_2'}}])
        self.assertRegex(output, r'STARTED\s+Plan architecture \(architecture\) #2')
        self.assertIn('RUN CANCELLED', self.consume([{'sequence': 8, 'type': 'run.cancelled', 'data': {}}]))
        self.assertEqual(json.loads(cursor.read_text()), {'version': 1, 'after': 2})

    def test_component_names_preserve_the_workflow_name_and_run_suffix(self):
        xml = '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="PcbEngineer"/>'
        name = workflow_name(xml, '/project/workflow.bpmn')
        run_id = 'abcdef0123456789abcdef0123456789'
        self.assertEqual(component_names(name, run_id), {
            'engine': f'pcb-engineer-workflow-{run_id[:16]}', 'workers': f'pcb-engineer-workers-{run_id[:16]}'})


if __name__ == '__main__':
    unittest.main()
