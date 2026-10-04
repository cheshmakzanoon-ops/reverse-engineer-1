"""Bounded/restartable inventory tests. Fixtures are not original Unity bundles."""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.content_fixture import bundle_fixture
from tools.content_pipeline import ContentError, canonical, verify_catalog
from tools import content_batches as batches


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root, self.out, self.checkpoints = (self.base / x for x in ('input', 'out', 'checkpoints'))
        self.loader, self.decoder, self.objects = bundle_fixture(self.root)
        self.calls = []

    def process(self, root, source, folder):
        from tools.content_unity import read_bundle
        self.calls.append(source['path'])
        return read_bundle(root, source, folder, loader=self.loader, mesh_decoder=self.decoder)

    def run_inventory(self, **kwargs):
        return batches.inventory_batched(self.root, self.out, self.checkpoints,
                                         _processor=self.process, **kwargs)

    def test_stop_then_resume_does_not_decode_completed_bundle_again(self):
        report = self.run_inventory(max_bundles=1)
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['completed_bundles'], 1)
        self.assertFalse(self.out.exists())
        report = self.run_inventory(resume=True)
        self.assertEqual(report['status'], 'indexed')
        self.assertEqual(self.calls, ['assets.bundle', 'scene.bundle'])
        self.assertEqual(verify_catalog(self.out)['counts']['objects'], 11)

    def test_resumed_catalog_is_byte_identical_to_unbatched(self):
        from tools.content_unity import inventory
        reference = self.base / 'reference'
        inventory(self.root, reference, loader=self.loader, mesh_decoder=self.decoder)
        self.run_inventory(max_bundles=1)
        self.run_inventory(resume=True)
        files = lambda p: {f.relative_to(p).as_posix(): f.read_bytes() for f in p.rglob('*') if f.is_file()}
        self.assertEqual(files(reference), files(self.out))

    def test_source_mutation_and_added_auxiliary_file_reject_resume(self):
        self.run_inventory(max_bundles=1)
        for action in ('mutate', 'add'):
            path = self.root / ('catalog.json' if action == 'mutate' else 'new.resS')
            prior = path.read_bytes() if path.exists() else None
            path.write_bytes(b'changed source')
            with self.subTest(action=action), self.assertRaises(ContentError):
                self.run_inventory(resume=True)
            if prior is None: path.unlink()
            else: path.write_bytes(prior)
        self.assertFalse(self.out.exists())

    def test_tampered_receipt_or_payload_is_not_reused(self):
        self.run_inventory(max_bundles=1)
        receipt = next((self.checkpoints / 'bundles').glob('*/batch.json'))
        payload = next(receipt.parent.glob('objects/*.bin'))
        original = payload.read_bytes()
        payload.write_bytes(b'tampered')
        with self.assertRaises(ContentError): self.run_inventory(resume=True)
        payload.write_bytes(original)
        original = receipt.read_bytes()
        receipt.write_bytes(original + b' ')
        with self.assertRaises(ContentError): self.run_inventory(resume=True)
        self.assertFalse(self.out.exists())

    def test_stale_decoder_recipe_is_not_reused(self):
        self.run_inventory(max_bundles=1)
        with patch.object(batches, 'recipe', return_value={'changed': True}), self.assertRaises(ContentError):
            self.run_inventory(resume=True)

    def test_worker_failure_keeps_good_bundle_and_retries_only_failure(self):
        real = self.process
        def fail(root, source, folder):
            if source['path'] == 'scene.bundle': raise RuntimeError('private raw value')
            return real(root, source, folder)
        first = batches.inventory_batched(self.root, self.out, self.checkpoints, _processor=fail)
        self.assertEqual(first['completed_bundles'], 1)
        self.assertEqual(first['status'], 'incomplete')
        self.assertNotIn('private raw value', json.dumps(first))
        self.assertFalse(self.out.exists())
        self.run_inventory(resume=True)
        self.assertEqual(self.calls, ['assets.bundle', 'scene.bundle'])

    def test_checkpoint_without_resume_and_existing_output_are_not_overwritten(self):
        self.run_inventory(max_bundles=1)
        with self.assertRaises(ContentError): self.run_inventory()
        self.out.mkdir()
        sentinel = self.out / 'keep'; sentinel.write_bytes(b'keep')
        with self.assertRaises(ContentError): self.run_inventory(resume=True)
        self.assertEqual(sentinel.read_bytes(), b'keep')

    def test_paths_cannot_overlap_or_follow_symlinks(self):
        for cp in (self.root / 'cp', self.out / 'cp', self.root.parent):
            with self.subTest(cp=str(cp)), self.assertRaises(ContentError):
                batches.inventory_batched(self.root, self.out, cp, _processor=self.process)
        link = self.base / 'linked'; link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ContentError):
            batches.inventory_batched(link, self.out, self.checkpoints, _processor=self.process)

    def test_invalid_budgets_fail_before_creating_state(self):
        for key, value in [('bundle_timeout', 0), ('bundle_timeout', float('nan')),
                           ('budget', -1), ('max_bundles', 0), ('max_bundles', True)]:
            with self.subTest(key=key, value=value), self.assertRaises(ContentError):
                self.run_inventory(**{key: value})
        self.assertFalse(self.checkpoints.exists())

    def test_missing_cross_bundle_reference_is_incomplete_not_substituted(self):
        self.objects['scene.bundle'][-1].tree['m_Mesh']['m_FileID'] = 9
        report = self.run_inventory()
        self.assertEqual(report['status'], 'incomplete')
        cat = verify_catalog(self.out)
        self.assertGreater(cat['counts']['errors'], 0)
        self.assertFalse(cat['original_media_complete'])

    def test_completed_job_can_be_verified_without_reprocessing(self):
        self.run_inventory()
        again = self.run_inventory(resume=True)
        self.assertEqual(again['status'], 'indexed')
        self.assertEqual(self.calls, ['assets.bundle', 'scene.bundle'])

    def test_unrecorded_complete_batch_is_recovered_after_parent_interruption(self):
        self.run_inventory(max_bundles=1)
        state = self.checkpoints / 'state.json'
        value = json.loads(state.read_text()); value['completed'] = {}
        state.write_bytes(canonical(value))
        self.run_inventory(resume=True)
        self.assertEqual(self.calls, ['assets.bundle', 'scene.bundle'])

    def test_public_report_contains_no_raw_record_or_absolute_workspace(self):
        report_path = self.base / 'public' / 'progress.json'
        self.run_inventory(max_bundles=1, report_path=report_path)
        text = report_path.read_text()
        for value in ('FixtureRoot', 'm_Vertices', str(self.base), 'raw-object', 'typetree'):
            self.assertNotIn(value, text)
        self.assertFalse(json.loads(text)['original_media_complete'])


    def test_exhausted_budget_keeps_every_bundle_pending(self):
        report = self.run_inventory(budget=1e-9)
        self.assertEqual(report['attempted_bundles'], 0)
        self.assertEqual(report['completed_bundles'], 0)
        self.assertEqual(len(report['pending_bundles']), 2)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())

    def test_checkpoint_lock_rejects_concurrent_writer(self):
        self.run_inventory(max_bundles=1)
        with batches.exclusive(self.checkpoints), self.assertRaisesRegex(ContentError, 'Another inventory'):
            self.run_inventory(resume=True)

    def test_unknown_ledger_bundle_and_modified_final_catalog_fail(self):
        self.run_inventory()
        cat = self.out / 'catalog.json'; cat.write_bytes(cat.read_bytes() + b' ')
        with self.assertRaises(ContentError): self.run_inventory(resume=True)
        state_path = self.checkpoints / 'state.json'; state = json.loads(state_path.read_text())
        state['completed']['unknown'] = {}
        state_path.write_bytes(canonical(state))
        with self.assertRaisesRegex(ContentError, 'Unknown bundle'): self.run_inventory(resume=True)

    def test_report_cannot_be_written_inside_original(self):
        with self.assertRaises(ContentError): self.run_inventory(report_path=self.root / 'progress.json')
        self.assertFalse((self.root / 'progress.json').exists())

    def test_cancelled_attempt_is_not_a_sealed_bundle(self):
        real = self.process
        def interrupted(root, source, folder):
            if source['path'] == 'scene.bundle':
                (folder / 'unfinished.bin').write_bytes(b'not complete')
                raise KeyboardInterrupt
            return real(root, source, folder)
        with self.assertRaises(KeyboardInterrupt):
            batches.inventory_batched(self.root, self.out, self.checkpoints, _processor=interrupted)
        self.assertFalse(self.out.exists())
        self.run_inventory(resume=True)
        self.assertEqual(self.calls, ['assets.bundle', 'scene.bundle'])

    def test_finalizer_runs_outside_repository_cwd(self):
        import os
        self.run_inventory(max_bundles=1)
        previous = Path.cwd()
        try:
            os.chdir(self.base)
            self.run_inventory(resume=True)
        finally:
            os.chdir(previous)
        self.assertEqual(verify_catalog(self.out)['counts']['objects'], 11)

    def test_finalizer_timeout_keeps_batches_for_resume(self):
        real = batches.run_bounded
        def timeout(command, log, timeout):
            if 'finalize' in command: raise subprocess.TimeoutExpired(command, timeout)
            return real(command, log, timeout)
        with patch.object(batches, 'run_bounded', side_effect=timeout):
            report = self.run_inventory()
        self.assertEqual(report['phase'], 'awaiting-finalization')
        self.assertFalse(self.out.exists())
        self.run_inventory(resume=True)
        self.assertEqual(self.calls, ['assets.bundle', 'scene.bundle'])

    def test_failed_worker_exit_code_is_recorded_without_false_completion(self):
        with patch.object(batches, '_command', return_value=[sys.executable, '-c', 'raise SystemExit(7)']):
            report = batches.inventory_batched(self.root, self.out, self.checkpoints)
        self.assertEqual(report['completed_bundles'], 0)
        self.assertEqual([f['exit_code'] for f in report['failures']], [7, 7])
        self.assertFalse(self.out.exists())

    def test_bundle_timeout_reports_cursor_without_unsealed_catalog(self):
        worker = self.base / 'hang.py'
        worker.write_text("import sys,time,json; from pathlib import Path; p=Path(sys.argv[-1]); "
                          "(p/'cursor.json').write_text(json.dumps({'phase':'image','objects_done':2,"
                          "'object_id':'a'*64,'object_type':'Texture2D','raw':'PRIVATE'})); time.sleep(30)")
        with patch.object(batches, '_command', side_effect=lambda *a: [sys.executable, str(worker), a[-1]]):
            report = batches.inventory_batched(self.root, self.out, self.checkpoints, bundle_timeout=1.0)
        self.assertFalse(self.out.exists())
        self.assertEqual(report['completed_bundles'], 0)
        self.assertEqual(len(report['failures']), 2)
        self.assertEqual(report['failures'][0]['reason'], 'timeout')
        self.assertEqual(report['failures'][0]['cursor']['phase'], 'image')
        self.assertNotIn('PRIVATE', json.dumps(report))



class ProcessTests(unittest.TestCase):
    def test_actual_child_timeout_preserves_log_and_returns(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'child.log'
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                batches.run_bounded([sys.executable, '-u', '-c', 'import time; print("started"); time.sleep(30)'], log, 1.0)
            self.assertLess(time.monotonic() - started, 5)
            self.assertIn('started', log.read_text())


    def test_timeout_stops_grandchild_heartbeat(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); heartbeat = root / 'heartbeat'
            child = "from pathlib import Path; import time; p=Path("+repr(str(heartbeat))+"); " + "\nwhile True: p.write_text(str(time.monotonic())); time.sleep(.05)"
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',"+repr(child)+"]); time.sleep(30)"
            with self.assertRaises(subprocess.TimeoutExpired):
                batches.run_bounded([sys.executable, '-c', parent], root / 'child.log', 1.0)
            first = heartbeat.read_text()
            time.sleep(.2)
            self.assertEqual(heartbeat.read_text(), first)

    def test_actual_nonzero_exit_is_not_success(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(subprocess.CalledProcessError):
            batches.run_bounded([sys.executable, '-c', 'raise SystemExit(3)'], Path(d) / 'child.log', 5)

    def test_actual_worker_rejects_pointer_without_a_success_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'input'; root.mkdir()
            (root / 'fake.bundle').write_text('version https://git-lfs.github.com/spec/v1\n')
            result = subprocess.run([sys.executable, '-m', 'tools.recover_content', 'inventory',
                                     str(root), str(Path(d) / 'out'), '--checkpoints', str(Path(d) / 'cp')],
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(d) / 'out').exists())
            self.assertIn('pointer', result.stderr)



class IntegrationTests(unittest.TestCase):

    def test_cursor_allowlist_rejects_payloads_inside_named_fields(self):
        self.assertEqual(batches.safe_cursor({'phase': 'PRIVATE PAYLOAD', 'objects_done': 'bytes',
                          'object_id': '../private/path', 'object_type': 'Texture2D\nPRIVATE'}), {})

    def test_timeout_options_are_not_silently_ignored_without_checkpoints(self):
        from tools import recover_content
        for option in ('--bundle-timeout', '--budget', '--max-bundles'):
            with self.subTest(option=option), patch.object(recover_content, 'inventory') as legacy:
                code = recover_content.main(['inventory', 'input', 'output', option, '1'])
                self.assertEqual(code, 1)
                legacy.assert_not_called()

    def test_source_command_connects_private_checkpoints_and_public_progress(self):
        from scripts.recover_source import inventory_command
        command = inventory_command(Path('/private/extracted/Game.app/Contents'), Path('/private/catalog'),
            Path('/private/checkpoints'), Path('/reports/progress.json'), bundle_timeout=12, budget=300)
        self.assertEqual(command[command.index('--checkpoints')+1], '/private/checkpoints')
        self.assertEqual(command[command.index('--progress-report')+1], '/reports/progress.json')
        self.assertEqual(command[command.index('--budget')+1], '300')

    def test_cli_rejects_resume_without_checkpoints(self):
        result = subprocess.run([sys.executable, '-m', 'tools.recover_content', 'inventory',
                                 'source', 'output', '--resume'], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('require --checkpoints', result.stderr)

if __name__ == '__main__': unittest.main()
