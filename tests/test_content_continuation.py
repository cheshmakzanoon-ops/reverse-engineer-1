"""Same-job continuation tests; synthetic readers are never original game media."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.content_fixture import bundle_fixture
from tools import content_batches as batches
from tools.content_pipeline import ContentError, canonical, verify_catalog


def paused(completed=0, total=2, objects_done=0, reason='timeout'):
    return {'schema': 1, 'status': 'incomplete', 'phase': 'paused',
            'total_bundles': total, 'completed_bundles': completed,
            'attempted_bundles': 1, 'reused_bundles': completed,
            'source_status': 'synthetic-adapter-fixture',
            'failures': [] if reason is None else [{'bundle': 'scene.bundle', 'reason': reason,
                         'cursor': {'objects_done': objects_done, 'phase': 'typetree'}}],
            'original_media_complete': False, 'godot_imported': False}


def finished(status='indexed'):
    result = paused(2, reason=None)
    result.update(status=status, phase='finished', counts={'bundles': 2, 'objects': 11, 'errors': 0 if status=='indexed' else 1})
    return result


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root, self.out, self.cp = (self.base / name for name in ('input', 'out', 'checkpoints'))
        self.loader, self.decoder, self.objects = bundle_fixture(self.root)
        self.report = self.base / 'reports' / 'continuation.json'

    def continuation(self, **kwargs):
        from tools.content_continuation import inventory_continued
        return inventory_continued(self.root, self.out, self.cp, continuation_report=self.report, **kwargs)

    def mock_run(self, reports, **kwargs):
        with patch('tools.content_continuation.inventory_batched', side_effect=reports) as runner:
            result = self.continuation(**kwargs)
        return result, runner

    def test_one_pass_pause_continues_with_explicit_resume_and_history(self):
        result, runner = self.mock_run([paused(1), finished()])
        self.assertEqual(result['status'], 'indexed')
        self.assertFalse(runner.call_args_list[0].kwargs['resume'])
        self.assertTrue(runner.call_args_list[1].kwargs['resume'])
        history = json.loads(self.report.read_text())
        self.assertEqual(history['stop_reason'], 'catalog-finished')
        self.assertEqual([p['completed_bundles'] for p in history['passes']], [1, 2])
        self.assertFalse(history['original_media_complete'])
        self.assertFalse(history['godot_imported'])

    def test_explicit_initial_resume_is_preserved(self):
        _, runner = self.mock_run([finished()], resume=True)
        self.assertTrue(runner.call_args.kwargs['resume'])

    def test_catalog_incomplete_is_terminal_not_retried_or_promoted(self):
        result, runner = self.mock_run([finished('incomplete')])
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(json.loads(self.report.read_text())['stop_reason'], 'catalog-finished')

    def test_integrity_exception_is_not_retried(self):
        with patch('tools.content_continuation.inventory_batched', side_effect=ContentError('private diagnostic')) as run:
            with self.assertRaises(ContentError): self.continuation()
        self.assertEqual(run.call_count, 1)
        text = self.report.read_text()
        self.assertNotIn('private diagnostic', text)
        self.assertEqual(json.loads(text)['stop_reason'], 'exception')

    def test_worker_non_timeout_failure_is_terminal(self):
        result, runner = self.mock_run([paused(1, reason='CalledProcessError')])
        self.assertEqual(result['status'], 'incomplete'); self.assertEqual(runner.call_count, 1)
        self.assertEqual(json.loads(self.report.read_text())['stop_reason'], 'non-retryable-failure')

    def test_finalizer_nonzero_exit_is_not_retried(self):
        report = paused(2, reason=None)
        report.update(phase='awaiting-finalization', failures=[{'phase': 'finalizing', 'reason': 'CalledProcessError'}])
        _, runner = self.mock_run([report]); self.assertEqual(runner.call_count, 1)

    def test_partial_object_progress_prevents_premature_stall(self):
        _, runner = self.mock_run([paused(0, objects_done=n) for n in (1, 2, 3)] + [finished()], stall_limit=2)
        self.assertEqual(runner.call_count, 4)

    def test_unchanged_object_progress_stops_without_claiming_success(self):
        result, runner = self.mock_run([paused(0, objects_done=0)] * 5, stall_limit=2)
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(json.loads(self.report.read_text())['stop_reason'], 'no-progress')

    def test_changing_phase_only_is_not_progress(self):
        a = paused(0, objects_done=1); b = copy.deepcopy(a)
        b['failures'][0]['cursor']['phase'] = 'image'
        _, runner = self.mock_run([a, b, a, b], stall_limit=2)
        self.assertEqual(runner.call_count, 3)

    def test_bundle_timeout_backoff_is_capped(self):
        _, runner = self.mock_run([paused(0, objects_done=n) for n in (1, 2, 3, 4)] + [finished()],
                                 bundle_timeout=12, max_bundle_timeout=30)
        self.assertEqual([c.kwargs['bundle_timeout'] for c in runner.call_args_list], [12, 24, 30, 30, 30])

    def test_max_passes_is_a_hard_attempt_bound(self):
        result, runner = self.mock_run([paused(0, objects_done=i) for i in range(1, 9)], max_passes=2)
        self.assertEqual(runner.call_count, 2); self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(json.loads(self.report.read_text())['stop_reason'], 'pass-limit')

    def test_total_budget_is_not_reset_between_passes(self):
        now = [0.0]; seen = []
        def run(*args, **kwargs):
            seen.append(kwargs['budget']); now[0] += 6
            return paused(0, objects_done=len(seen))
        with patch('tools.content_continuation.time.monotonic', side_effect=lambda: now[0]), \
             patch('tools.content_continuation.inventory_batched', side_effect=run):
            result = self.continuation(budget=10, pass_budget=8)
        self.assertEqual(seen, [8, 4])
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(json.loads(self.report.read_text())['stop_reason'], 'budget-exhausted')

    def test_all_sealed_bundles_give_finalizer_remaining_budget(self):
        now = [0.0]; seen = []
        def run(*args, **kwargs):
            seen.append(kwargs['budget']); now[0] += 20
            if len(seen) == 1:
                value = paused(2, reason=None); value['phase'] = 'awaiting-finalization'; return value
            return finished()
        with patch('tools.content_continuation.time.monotonic', side_effect=lambda: now[0]), \
             patch('tools.content_continuation.inventory_batched', side_effect=run):
            self.continuation(budget=100, pass_budget=25)
        self.assertEqual(seen, [25, 80])

    def test_report_contains_only_approved_metrics_not_private_worker_payload(self):
        value = paused(1); value['secret'] = 'private bytes'
        value['failures'][0]['secret'] = 'private bytes'
        result, _ = self.mock_run([value, finished()])
        self.assertNotIn('private bytes', self.report.read_text())
        self.assertNotIn('private bytes', canonical(result).decode())

    def test_invalid_policy_fails_before_checkpoint_or_report_creation(self):
        cases = [('budget', 0), ('budget', float('inf')), ('pass_budget', -1), ('pass_budget', float('nan')),
                 ('bundle_timeout', 0), ('max_bundle_timeout', 1), ('max_passes', 0), ('max_passes', True),
                 ('stall_limit', 0), ('max_passes', 1.5)]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ContentError):
                self.continuation(**{key: value})
        self.assertFalse(self.cp.exists()); self.assertFalse(self.report.exists())

    def test_report_cannot_overlap_media_or_worker_progress(self):
        from tools.content_continuation import inventory_continued
        for target in (self.root/'r.json', self.cp/'r.json', self.out/'r.json', self.base/'same.json'):
            with self.subTest(target=target), self.assertRaises(ContentError):
                inventory_continued(self.root, self.out, self.cp, continuation_report=target, report_path=target)

    def test_existing_history_not_overwritten(self):
        self.report.parent.mkdir(); self.report.write_text('keep')
        with self.assertRaises(ContentError): self.continuation()
        self.assertEqual(self.report.read_text(), 'keep')

    def test_competing_controllers_fail_without_entering_decoder(self):
        from tools.content_continuation import controller_lock
        self.cp.parent.mkdir(parents=True, exist_ok=True)
        with controller_lock(self.cp), patch('tools.content_continuation.inventory_batched') as run:
            with self.assertRaises(ContentError): self.continuation()
        run.assert_not_called()

    def test_real_two_bundle_pipeline_resumes_and_matches_single_pass(self):
        from tools.content_unity import read_bundle, inventory
        expected = self.base/'expected'
        inventory(self.root, expected, loader=self.loader, mesh_decoder=self.decoder)
        calls = []; invocations = []
        def process(root, source, folder):
            calls.append(source['path'])
            return read_bundle(root, source, folder, loader=self.loader, mesh_decoder=self.decoder)
        def run(*args, **kwargs):
            invocations.append(kwargs['resume'])
            return batches.inventory_batched(*args, **kwargs, _processor=process, max_bundles=1)
        with patch('tools.content_continuation.inventory_batched', side_effect=run):
            result = self.continuation()
        self.assertEqual(result['status'], 'indexed'); self.assertEqual(invocations, [False, True])
        self.assertEqual(calls, ['assets.bundle', 'scene.bundle'])
        file_bytes = lambda p: {f.relative_to(p).as_posix(): f.read_bytes() for f in p.rglob('*') if f.is_file()}
        self.assertEqual(file_bytes(expected), file_bytes(self.out))
        self.assertEqual(verify_catalog(self.out)['counts']['objects'], 11)

    def test_real_tampered_completed_payload_is_not_reused_next_pass(self):
        from tools.content_unity import read_bundle
        calls = []
        def process(root, source, folder):
            calls.append(source['path']); return read_bundle(root, source, folder, loader=self.loader, mesh_decoder=self.decoder)
        def run(*args, **kwargs):
            result = batches.inventory_batched(*args, **kwargs, _processor=process, max_bundles=1)
            if not kwargs['resume']:
                next((self.cp/'bundles').glob('*/objects/*.bin')).write_bytes(b'corrupt')
            return result
        with patch('tools.content_continuation.inventory_batched', side_effect=run), self.assertRaises(ContentError):
            self.continuation()
        self.assertEqual(calls, ['assets.bundle']); self.assertFalse(self.out.exists())

    def test_222_authored_bundles_all_required_before_catalog_is_published(self):
        # Deliberately authored scale fixture: one independent object per file.
        # This never purports to decode the original game's 222 bundles.
        import shutil
        from tools.content_unity import read_bundle
        shutil.rmtree(self.root); self.root.mkdir()
        calls = []
        class Obj:
            path_id = 1
            type = SimpleNamespace(name='GameObject')
            def __init__(self, name): self.assets_file = SimpleNamespace(name='CAB-'+name, externals=[])
            def get_raw_data(self): return b'authored fixture bytes'
            def parse_as_dict(self): return {'m_Name': 'Authored fixture'}
        for n in range(222): (self.root/f'{n:03}.bundle').write_bytes(b'NOT A UNITY BUNDLE')
        def process(root, source, folder):
            calls.append(source['path'])
            return read_bundle(root, source, folder, loader=lambda p: SimpleNamespace(objects=[Obj(Path(p).stem)]))
        def run(*args, **kwargs):
            result = batches.inventory_batched(*args, **kwargs, max_bundles=37, _processor=process)
            if len(calls) < 222: self.assertFalse(self.out.exists())
            return result
        with patch('tools.content_continuation.inventory_batched', side_effect=run):
            result = self.continuation(max_passes=6)
        self.assertEqual(result['status'], 'indexed'); self.assertEqual(len(set(calls)), 222)
        cat = verify_catalog(self.out)
        self.assertEqual(cat['counts']['bundles'], 222); self.assertEqual(cat['counts']['objects'], 222)
        self.assertFalse(cat['original_media_complete']); self.assertFalse(cat['godot_imported'])

    def test_cli_requires_checkpoints_and_explicit_continuation(self):
        from tools import recover_content
        for options in (['--continue-until-complete'], ['--pass-budget', '1'],
                        ['--checkpoints', str(self.cp), '--pass-budget', '1']):
            with self.subTest(options=options), patch.object(recover_content, 'inventory') as legacy:
                code = recover_content.main(['inventory', str(self.root), str(self.out), *options])
                self.assertEqual(code, 1); legacy.assert_not_called()

    def test_cli_routes_same_job_options(self):
        from tools import recover_content
        with patch('tools.content_continuation.inventory_continued', return_value=finished()) as run:
            code = recover_content.main(['inventory', str(self.root), str(self.out), '--checkpoints', str(self.cp),
                '--continue-until-complete', '--pass-budget', '90', '--budget', '350', '--max-passes', '4',
                '--max-bundle-timeout', '240', '--continuation-report', str(self.report)])
        self.assertEqual(code, 0); self.assertEqual(run.call_args.kwargs['budget'], 350)
        self.assertEqual(run.call_args.kwargs['pass_budget'], 90); self.assertEqual(run.call_args.kwargs['max_passes'], 4)

    def test_decoder_recipe_is_unchanged_by_orchestration(self):
        names = set(batches.recipe()['code'])
        self.assertNotIn('content_continuation.py', names)
        self.assertNotIn('recover_content.py', names)
        self.assertEqual(names, {'content_batches.py', 'content_unity.py', 'content_pipeline.py', 'content_objects.py', 'content_textures.py'})

    def test_keyboard_interrupt_is_retained_and_not_retried(self):
        with patch('tools.content_continuation.inventory_batched', side_effect=KeyboardInterrupt) as run:
            with self.assertRaises(KeyboardInterrupt): self.continuation()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads(self.report.read_text())['stop_reason'], 'interrupted')

    def test_forged_finished_pass_with_missing_bundle_is_rejected(self):
        value = finished(); value['completed_bundles'] = 1
        with self.assertRaises(ContentError): self.mock_run([value])
        self.assertEqual(json.loads(self.report.read_text())['status'], 'incomplete')

    def test_zero_time_left_does_not_start_another_worker(self):
        now = [0.0]
        def run(*args, **kwargs):
            now[0] = 100
            return paused(0, objects_done=1)
        with patch('tools.content_continuation.time.monotonic', side_effect=lambda: now[0]), \
             patch('tools.content_continuation.inventory_batched', side_effect=run) as worker:
            self.continuation(budget=100)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(json.loads(self.report.read_text())['stop_reason'], 'budget-exhausted')

    def test_terminal_content_failure_cannot_be_masked_by_prior_timeout(self):
        result, run = self.mock_run([paused(1), finished('incomplete')])
        self.assertEqual(run.call_count, 2); self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['counts']['errors'], 1)

    def test_real_worker_timeout_then_retry_keeps_completed_asset_bundle(self):
        from tools.content_unity import read_bundle
        calls = []; failed_once = [False]
        def process(root, source, folder):
            calls.append(source['path'])
            if source['path'] == 'scene.bundle' and not failed_once[0]:
                failed_once[0] = True
                batches.run_bounded([sys.executable, '-c', 'import time; time.sleep(30)'],
                                    folder/'authored-timeout.log', .3)
            return read_bundle(root, source, folder, loader=self.loader, mesh_decoder=self.decoder)
        def run(*args, **kwargs):
            return batches.inventory_batched(*args, **kwargs, _processor=process)
        with patch('tools.content_continuation.inventory_batched', side_effect=run):
            result = self.continuation()
        self.assertEqual(calls, ['assets.bundle', 'scene.bundle', 'scene.bundle'])
        self.assertEqual(result['status'], 'indexed')
        self.assertEqual(result['continuation']['passes'][0]['failures'][0]['reason'], 'timeout')


class SourceContinuationWiringTests(unittest.TestCase):
    def test_source_runner_passes_explicit_continuation_policy(self):
        from scripts.recover_source import inventory_command
        command = inventory_command(Path('/private/Contents'), Path('/private/out'), Path('/private/checkpoints'),
            Path('/reports/progress.json'), bundle_timeout=50, budget=1500, pass_budget=500,
            max_passes=7, max_bundle_timeout=400)
        self.assertIn('--continue-until-complete', command)
        for flag, expected in [('--budget', '1500'), ('--pass-budget', '500'), ('--max-passes', '7'),
                               ('--max-bundle-timeout', '400'),
                               ('--continuation-report', '/reports/inventory-continuation.json')]:
            self.assertEqual(command[command.index(flag)+1], expected)

    def test_invalid_source_continuation_policy_fails_before_reading_original(self):
        from scripts import recover_source
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)/'work'; report = Path(d)/'reports'
            with patch.object(recover_source, 'verify_source') as verify:
                rc = recover_source.main(['--dmg', str(Path(d)/'source'), '--workspace', str(work),
                    '--reports', str(report), '--inventory-max-passes', '0'])
            self.assertEqual(rc, 1); verify.assert_not_called(); self.assertFalse(work.exists())

    def test_workflow_uses_bounded_policy_and_preserves_history_before_cleanup(self):
        # Test the relevant wiring without adding a YAML dependency to runners.
        import re
        root = Path(__file__).resolve().parents[1]
        text = (root/'.github/workflows/source-recovery.yml').read_text()
        self.assertIn('--inventory-budget 3600 --inventory-pass-budget 900', text)
        self.assertIn('--inventory-max-passes 8', text)
        self.assertIn('--max-bundle-timeout 480', text)
        preserve = text.index('name: Preserve metadata and diagnostics only')
        cleanup = text.index('name: Remove ephemeral original media')
        self.assertLess(preserve, cleanup)
        block = text[preserve:cleanup]
        self.assertIn('verification/source/inventory-continuation.json', block)
        timeout = int(re.search(r'timeout-minutes: (\d+)', text).group(1))
        self.assertGreater(timeout*60, 3600+600)
        self.assertNotIn('path: ${{ runner.temp }}/kart-original', block)


if __name__ == '__main__': unittest.main()
