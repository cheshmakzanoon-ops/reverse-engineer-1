"""Source-runner finalization policy and integration using authored inputs only."""
import copy
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import recover_source as runner
from tools.content_pipeline import ContentError, canonical


class SourceFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.progress = {'schema': 1, 'status': 'incomplete', 'phase': 'awaiting-finalization',
                         'total_bundles': 222, 'completed_bundles': 222,
                         'failures': [{'phase': 'finalizing', 'reason': 'TimeoutExpired'}]}

    def test_all_sealed_timeout_is_eligible(self):
        self.assertTrue(runner.finalization_eligible(self.progress))

    def test_all_sealed_budget_boundary_without_failure_is_eligible(self):
        self.progress['failures'] = []
        self.assertTrue(runner.finalization_eligible(self.progress))

    def test_partial_bundles_never_enter_finalizer(self):
        self.progress['completed_bundles'] = 221
        self.assertFalse(runner.finalization_eligible(self.progress))

    def test_incomplete_finished_catalog_is_not_retried_into_success(self):
        self.progress['phase'] = 'finished'
        self.assertFalse(runner.finalization_eligible(self.progress))

    def test_integrity_and_non_timeout_failures_are_terminal(self):
        for reason in ('ContentError', 'CalledProcessError', 'OSError', 'ValueError', 'unknown'):
            with self.subTest(reason=reason):
                self.progress['failures'][0]['reason'] = reason
                self.assertFalse(runner.finalization_eligible(self.progress))

    def test_bundle_timeout_is_not_finalization_timeout(self):
        self.progress['failures'][0] = {'bundle': 'a.bundle', 'reason': 'timeout'}
        self.assertFalse(runner.finalization_eligible(self.progress))

    def test_malformed_progress_is_ineligible(self):
        for value in (None, [], '', {}, {'schema': 1}, 1):
            with self.subTest(value=value):
                self.assertFalse(runner.finalization_eligible(value))
        for field, bad in [('schema', 2), ('status', 'indexed'), ('phase', 'paused'),
                           ('total_bundles', True), ('completed_bundles', 222.0),
                           ('total_bundles', 0), ('failures', None), ('failures', ['timeout'])]:
            value = copy.deepcopy(self.progress); value[field] = bad
            with self.subTest(field=field, bad=bad):
                self.assertFalse(runner.finalization_eligible(value))

    def test_command_uses_separate_budget_and_private_workspace(self):
        args = runner.finalization_command(self.root/'Content', self.root/'catalog', self.root/'cp',
                                          self.root/'assembly', self.root/'reports/progress.json', budget=1400)
        self.assertIn(str(runner.ROOT/'tools/content_finalize.py'), args)
        self.assertEqual(args[args.index('--budget')+1], '1400')
        self.assertNotIn('--resume', args)
        self.assertIn(str(self.root/'Content/Resources/Data'), args)
        self.assertIn(str(self.root/'assembly'), args)

    def test_existing_workspace_requires_explicit_resume_in_command(self):
        (self.root/'assembly').mkdir()
        args = runner.finalization_command(self.root/'Content', self.root/'catalog', self.root/'cp',
                                          self.root/'assembly', self.root/'report.json', budget=100)
        self.assertIn('--resume', args)

    def test_invalid_budget_fails_before_any_read(self):
        for bad in ('nan', 'inf', '-1', '0'):
            with self.subTest(bad=bad), patch.object(runner, 'verify_source') as verify, \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = runner.main(['--dmg', str(self.root/'missing'), '--workspace', str(self.root/'work'),
                                    '--reports', str(self.root/'reports'), '--finalization-budget', bad])
                self.assertEqual(code, 1); verify.assert_not_called()
                self.assertFalse((self.root/'work').exists())

    def simulate(self, *, progress=None, final_error=None, publish=True, catalog_status='indexed'):
        progress = self.progress if progress is None else progress
        workspace, reports = self.root/'work', self.root/'reports'
        content = self.root/'Fixture.app/Contents'
        commands = []
        def run(command, log, *, timeout):
            commands.append((command, log, timeout))
            if log.name == 'inventory.log':
                (reports/'inventory-progress.json').write_bytes(canonical(progress))
                raise ContentError('historical inventory timeout')
            if final_error:
                (reports/'finalization-progress.json').write_bytes(canonical({'schema':1,'status':'incomplete','phase':'paused'}))
                raise ContentError(final_error)
            if publish:
                folder = workspace/'content-catalog'; folder.mkdir()
                (folder/'catalog.json').write_bytes(canonical({'status':catalog_status,'objects':[],
                    'errors':[],'counts':{'bundles':222,'objects':11,'errors':0},'source_status':'synthetic-adapter-fixture'}))
                (reports/'finalization-progress.json').write_bytes(canonical({'schema':1,'status':catalog_status,'phase':'finished'}))
        with patch.object(runner, 'verify_source', return_value={'sha256':'fixture','bytes':8}), \
             patch.object(runner, 'extract_original'), patch.object(runner, 'find_application', return_value=content), \
             patch.object(runner, 'extracted_manifest', return_value=[{'path':'fixture','bytes':8,'sha256':'fixture'}]), \
             patch.object(runner, 'binary_metadata', return_value={'original_source_recovered':False}), \
             patch.object(runner, 'run', side_effect=run), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = runner.main(['--dmg', str(self.root/'fixture.dmg'), '--workspace', str(workspace),
                              '--reports', str(reports), '--finalization-budget', '1400'])
        return rc, json.loads((reports/'recovery.json').read_text()), commands

    def test_runner_can_complete_only_after_successful_finalization(self):
        rc, report, commands = self.simulate()
        self.assertEqual(rc, 0); self.assertTrue(report['inventory_complete'])
        self.assertEqual(report['status'], 'inventoried-not-ported')
        self.assertEqual(report['inventory_stage_error'], 'historical inventory timeout')
        self.assertEqual(len(commands), 2); self.assertEqual(commands[1][2], 1520)
        self.assertEqual(report['finalization_progress']['phase'], 'finished')
        for key in ('original_source_recovered', 'godot_imported', 'complete_game'):
            self.assertIs(report[key], False)

    def test_runner_retains_finalizer_failure(self):
        rc, report, commands = self.simulate(final_error='finalizer timed out')
        self.assertEqual(rc, 1); self.assertFalse(report['inventory_complete'])
        self.assertIn('finalizer timed out', report['error'])
        self.assertEqual(report['finalization_progress']['phase'], 'paused')

    def test_success_exit_without_catalog_is_not_success(self):
        rc, report, _ = self.simulate(publish=False)
        self.assertEqual(rc, 1); self.assertFalse(report['inventory_complete'])

    def test_incomplete_catalog_cannot_be_promoted(self):
        rc, report, _ = self.simulate(catalog_status='incomplete')
        self.assertEqual(rc, 1); self.assertFalse(report['inventory_complete'])

    def test_partial_decode_does_not_schedule_finalization(self):
        self.progress['completed_bundles'] = 221
        rc, report, commands = self.simulate()
        self.assertEqual(rc, 1); self.assertEqual(len(commands), 1)
        self.assertNotIn('finalization_progress', report)

    def test_non_timeout_failure_does_not_schedule_finalization(self):
        self.progress['failures'][0]['reason'] = 'ContentError'
        rc, _, commands = self.simulate()
        self.assertEqual(rc, 1); self.assertEqual(len(commands), 1)

    def test_workflow_includes_finalizer_tests_budget_and_metadata(self):
        text = (runner.ROOT/'.github/workflows/source-recovery.yml').read_text()
        for required in ('tools/content_finalize.py', 'tests/test_content_finalize.py',
                         'tests/test_source_finalization.py', 'tests.test_content_finalize',
                         'tests.test_source_finalization', '--finalization-budget 1800',
                         'verification/source/finalization-progress.json', 'verification/source/finalization.log'):
            self.assertIn(required, text)
        self.assertNotIn('path: ${{ runner.temp }}/kart-original/content-finalization', text)


if __name__ == '__main__':
    unittest.main()
