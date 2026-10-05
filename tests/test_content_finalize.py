"""Resumable finalization uses authored fixtures, not recovered production media."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.content_fixture import bundle_fixture
from tools import content_batches as batches
from tools import content_finalize as finalizer
from tools.content_pipeline import ContentError, canonical, verify_catalog, verify_source
from tools.content_unity import inventory, read_bundle


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root, self.out, self.cp, self.work = (self.base / n for n in ('input', 'out', 'cp', 'assembly'))
        self.report = self.base / 'reports' / 'finalization.json'
        self.loader, self.decoder, self.source_objects = bundle_fixture(self.root)
        self.decode_calls = []

    def processor(self, root, source, folder):
        self.decode_calls.append(source['path'])
        return read_bundle(root, source, folder, loader=self.loader, mesh_decoder=self.decoder)

    def seal(self):
        def fail(command, log, timeout):
            raise subprocess.TimeoutExpired(command, timeout)
        with patch.object(batches, 'run_bounded', side_effect=fail):
            report = batches.inventory_batched(self.root, self.out, self.cp, _processor=self.processor)
        self.assertEqual(report['phase'], 'awaiting-finalization')
        self.assertEqual(report['completed_bundles'], 2)

    def finish(self, **kwargs):
        return finalizer.finalize_catalog(self.root, self.out, self.cp, self.work,
                                          report_path=self.report, **kwargs)

    def interrupt(self, phase, **kwargs):
        def observe(report):
            if report['phase'] == phase:
                raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.finish(_observe=observe, **kwargs)

    def files(self, directory):
        return {p.relative_to(directory).as_posix(): p.read_bytes()
                for p in directory.rglob('*') if p.is_file()}

    def test_uninterrupted_matches_unchanged_original_finalizer_byte_for_byte(self):
        reference = self.base / 'reference'
        inventory(self.root, reference, loader=self.loader, mesh_decoder=self.decoder)
        self.seal()
        report = self.finish()
        self.assertEqual(report['status'], 'indexed')
        self.assertEqual(self.files(self.out), self.files(reference))
        self.assertEqual(len(self.decode_calls), 2)
        self.assertFalse(report['godot_imported'])
        self.assertFalse(report['original_source_recovered'])

    def test_raw_numeric_specials_match_original_finalizer(self):
        obj = self.source_objects['scene.bundle'][0]
        obj.tree['fixture_slope'] = float('inf')
        obj.get_raw_data = lambda: b'authored raw numeric fixture, not game bytes'
        reference = self.base / 'reference'
        inventory(self.root, reference, loader=self.loader, mesh_decoder=self.decoder)
        self.seal(); self.interrupt('font-ownership')
        self.assertEqual(self.finish(resume=True)['status'], 'indexed')
        self.assertEqual(self.files(self.out), self.files(reference))
        self.assertTrue(any(r.get('numeric_specials') for r in verify_catalog(self.out)['objects']))

    def test_empty_font_ownership_matches_original_finalizer(self):
        from tests.test_content_textures import empty_texture
        texture = self.source_objects['assets.bundle'][-1]
        texture.tree = empty_texture()
        font = type(texture)(texture.assets_file, 11, 'Font', {
            'm_Name': 'Authored font', 'm_FontData': b'authored non-font bytes',
            'm_CharacterRects': [], 'm_Texture': {'m_FileID': 0, 'm_PathID': 10}})
        self.source_objects['assets.bundle'].append(font)
        reference = self.base / 'reference'
        inventory(self.root, reference, loader=self.loader, mesh_decoder=self.decoder)
        self.seal(); self.interrupt('font-ownership')
        self.assertEqual(self.finish(resume=True)['status'], 'indexed')
        self.assertEqual(self.files(self.out), self.files(reference))
        record = next(r for r in verify_catalog(self.out)['objects'] if r['type'] == 'Texture2D')
        self.assertEqual(record['image_owners'][0]['kind'], 'embedded-font-atlas')
        self.assertFalse((self.out / 'images').exists())

    def test_valid_but_unrelated_published_catalog_is_rejected(self):
        self.seal(); self.finish()
        path = self.out / 'catalog.json'
        catalog = json.loads(path.read_text())
        # A fabricated extra bank does not invalidate the object graph itself.
        catalog['sources'].append({'path': 'other.bank', 'kind': 'bank', 'bytes': 1, 'sha256': '0' * 64})
        path.write_bytes(canonical(catalog))
        for receipt_path in (self.cp / 'final.json', self.work / 'ready.json'):
            receipt = json.loads(receipt_path.read_text()); receipt['manifest'] = verify_source(path)
            receipt_path.write_bytes(canonical(receipt))
        with self.assertRaisesRegex(ContentError, 'sealed inputs'):
            self.finish(resume=True)

    def test_legacy_resume_recognizes_new_final_catalog_and_receipt(self):
        self.seal(); self.finish()
        report = batches.inventory_batched(self.root, self.out, self.cp, resume=True,
                                           _processor=self.processor)
        self.assertEqual(report['status'], 'indexed')
        self.assertEqual(len(self.decode_calls), 2)

    def test_new_finalizer_is_not_part_of_original_decoder_recipe(self):
        expected = {'content_batches.py', 'content_unity.py', 'content_pipeline.py',
                    'content_objects.py', 'content_textures.py'}
        self.assertEqual(set(batches.recipe()['code']), expected)
        self.assertNotIn('content_finalize.py', batches.recipe()['code'])

    def test_copy_resume_reuses_verified_files_without_rewriting(self):
        self.seal()
        copied = []
        real = finalizer._copy_artifact
        def interrupted(*args):
            result = real(*args)
            if result: copied.append(args[1])
            if len(copied) == 3: raise KeyboardInterrupt
            return result
        with patch.object(finalizer, '_copy_artifact', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.finish()
        before = {p: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes()) for p in copied}
        report = self.finish(resume=True)
        self.assertGreaterEqual(report['reused_artifacts'], 3)
        for old, prior in before.items():
            new = self.out / old.relative_to(self.work / 'catalog')
            self.assertEqual((new.stat().st_ino, new.stat().st_mtime_ns, new.read_bytes()), prior)

    def test_resolved_chunks_are_reused_but_full_validator_still_runs(self):
        self.seal(); self.interrupt('font-ownership')
        with patch.object(finalizer, 'verify_catalog', wraps=verify_catalog) as verify:
            report = self.finish(resume=True)
        self.assertGreater(report['reused_edge_chunks'], 0)
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(report['status'], 'indexed')

    def test_published_copy_is_independent_of_checkpoint_payload(self):
        self.seal(); self.finish()
        batch = next((self.cp / 'bundles').glob('*/batch.json'))
        artifact = json.loads(batch.read_text())['objects'][0]['artifacts'][0]
        original = batch.parent / artifact['path']
        copied = self.out / artifact['path']
        self.assertNotEqual(original.stat().st_ino, copied.stat().st_ino)
        before = copied.read_bytes(); original.write_bytes(b'changed')
        self.assertEqual(copied.read_bytes(), before)

    def test_missing_bundle_is_rejected_without_workspace(self):
        batches.inventory_batched(self.root, self.out, self.cp, max_bundles=1, _processor=self.processor)
        with self.assertRaisesRegex(ContentError, 'every source bundle'):
            self.finish()
        self.assertFalse(self.work.exists()); self.assertFalse(self.out.exists())

    def test_changed_source_is_rejected(self):
        self.seal(); (self.root / 'new.resS').write_bytes(b'new')
        with self.assertRaisesRegex(ContentError, 'Original sources'):
            self.finish()

    def test_changed_decoder_is_rejected(self):
        self.seal()
        with patch.object(finalizer, 'recipe', return_value={'wrong': 1}), self.assertRaises(ContentError):
            self.finish()

    def test_changed_finalizer_is_rejected_on_resume(self):
        self.seal(); self.interrupt('copying')
        with patch.object(finalizer, 'finalizer_recipe', return_value={'wrong': 1}), self.assertRaises(ContentError):
            self.finish(resume=True)

    def test_resume_flags_are_explicit(self):
        self.seal()
        with self.assertRaisesRegex(ContentError, 'missing'): self.finish(resume=True)
        self.interrupt('copying')
        with self.assertRaisesRegex(ContentError, 'explicitly'): self.finish()

    def test_altered_checkpoint_payload_is_not_reused(self):
        self.seal(); self.interrupt('copying')
        path = next((self.cp / 'bundles').glob('*/objects/*.bin'))
        path.write_bytes(b'changed')
        with self.assertRaises(ContentError): self.finish(resume=True)
        self.assertFalse(self.out.exists())

    def test_altered_copied_payload_is_not_reused(self):
        self.seal(); self.interrupt('font-ownership')
        path = next((self.work / 'catalog' / 'objects').glob('*.bin'))
        path.write_bytes(b'changed')
        with self.assertRaises(ContentError): self.finish(resume=True)
        self.assertFalse(self.out.exists())

    def test_altered_edge_chunk_digest_is_not_reused(self):
        self.seal(); self.interrupt('font-ownership')
        path = self.work / 'edges' / '00000000.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(ContentError): self.finish(resume=True)
        self.assertFalse(self.out.exists())

    def test_forged_edges_with_updated_receipt_still_fail_full_validation(self):
        self.seal(); self.interrupt('font-ownership')
        path = self.work / 'edges' / '00000000.json'
        value = json.loads(path.read_text())
        row = next(r for r in value['rows'] if r['references'])
        row['references'] = []
        path.write_bytes(canonical(value))
        receipt = path.with_name('00000000.receipt.json')
        value = json.loads(receipt.read_text()); value.update(verify_source(path))
        receipt.write_bytes(canonical(value))
        with self.assertRaisesRegex(ContentError, 'edges disagree'): self.finish(resume=True)
        self.assertFalse(self.out.exists())

    def test_missing_chunk_receipt_recomputes_instead_of_trusting_partial(self):
        self.seal(); self.interrupt('font-ownership')
        (self.work / 'edges' / '00000000.receipt.json').unlink()
        (self.work / 'edges' / '00000000.json').write_bytes(b'partial')
        self.assertEqual(self.finish(resume=True)['status'], 'indexed')

    def test_interruption_after_rename_recovers_owned_published_output(self):
        self.seal(); self.interrupt('finished')
        self.assertTrue(self.out.is_dir())
        before = self.files(self.out)
        report = self.finish(resume=True)
        self.assertEqual(report['status'], 'indexed'); self.assertEqual(before, self.files(self.out))
        self.assertEqual(len(self.decode_calls), 2)

    def test_published_output_tampering_is_rejected(self):
        self.seal(); self.finish()
        (self.out / 'catalog.json').write_bytes((self.out / 'catalog.json').read_bytes() + b' ')
        with self.assertRaises(ContentError): self.finish(resume=True)

    def test_publication_interruption_retains_ready_catalog(self):
        self.seal(); self.interrupt('publishing')
        self.assertFalse(self.out.exists())
        self.assertTrue((self.work / 'ready.json').is_file())
        self.assertEqual(self.finish(resume=True)['status'], 'indexed')

    def test_existing_unowned_output_is_never_overwritten(self):
        self.seal(); self.out.mkdir(); (self.out / 'keep').write_bytes(b'keep')
        with self.assertRaises(ContentError): self.finish()
        self.assertEqual((self.out / 'keep').read_bytes(), b'keep')

    def test_unexpected_staged_entry_blocks_publication(self):
        self.seal(); self.interrupt('font-ownership')
        (self.work / 'catalog' / 'unrelated.txt').write_bytes(b'do not publish')
        with self.assertRaisesRegex(ContentError, 'Unexpected staged'): self.finish(resume=True)
        self.assertFalse(self.out.exists())

    def test_symlink_in_stage_is_rejected(self):
        self.seal(); self.interrupt('copying')
        (self.work / 'catalog' / 'objects').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ContentError): self.finish(resume=True)

    def test_overlapping_paths_and_invalid_budgets_fail_early(self):
        self.seal()
        for work in (self.root / 'work', self.cp / 'work', self.out / 'work'):
            with self.subTest(work=work), self.assertRaises(ContentError):
                finalizer.finalize_catalog(self.root, self.out, self.cp, work)
        for budget in (0, -1, True, float('nan'), float('inf')):
            with self.subTest(budget=budget), self.assertRaises(ContentError): self.finish(budget=budget)
        self.assertFalse(self.work.exists())

    def test_competing_decoder_and_controller_locks_reject(self):
        self.seal()
        with batches.exclusive(self.cp), self.assertRaises(ContentError): self.finish()
        with finalizer.controller_lock(self.cp), self.assertRaises(ContentError): self.finish()

    def test_cooperative_budget_pause_keeps_reusable_work(self):
        self.seal(); now = [0.0]
        def observe(report):
            if report['phase'] == 'resolving': now[0] = 10.0
        with patch.object(finalizer.time, 'monotonic', side_effect=lambda: now[0]):
            result = self.finish(budget=5, _observe=observe)
        self.assertEqual(result['phase'], 'paused'); self.assertFalse(self.out.exists())
        self.assertGreater(self.finish(resume=True)['reused_artifacts'], 0)

    def test_unresolved_original_reference_stays_incomplete_not_success(self):
        self.source_objects['scene.bundle'][-1].tree['m_Mesh']['m_FileID'] = 99
        self.seal(); report = self.finish()
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['phase'], 'finished')
        self.assertGreater(verify_catalog(self.out)['counts']['errors'], 0)

    def test_unknown_checkpoint_key_is_rejected(self):
        self.seal()
        state = self.cp / 'state.json'; value = json.loads(state.read_text())
        value['completed']['unknown'] = {}; state.write_bytes(canonical(value))
        with self.assertRaises(ContentError): self.finish()

    def test_public_progress_never_exposes_paths_or_payload(self):
        self.seal(); self.finish()
        text = self.report.read_text()
        for value in (str(self.base), 'FixtureRoot', 'm_Vertices', 'm_FileID', 'wrapped_key'):
            self.assertNotIn(value, text)

    def test_cli_outside_repo_cwd_and_completed_cli_resume(self):
        self.seal()
        command = [sys.executable, str(Path(finalizer.__file__)), str(self.root), str(self.out),
                   '--checkpoints', str(self.cp), '--workspace', str(self.work)]
        result = subprocess.run(command, cwd=self.base, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run(command + ['--resume'], cwd=self.base, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_sigkill_after_resolved_chunk_can_resume(self):
        self.seal()
        script = self.base / 'killable.py'
        repo = Path(finalizer.__file__).resolve().parents[1]
        script.write_text('import sys,time\nfrom pathlib import Path\n'
            + f'sys.path.insert(0, {str(repo)!r})\n'
            + 'from tools.content_finalize import finalize_catalog\n'
            + 'def observe(report):\n'
            + ' if report["phase"] == "font-ownership":\n'
            + '  print("READY", flush=True)\n  time.sleep(60)\n'
            + f'finalize_catalog(Path({str(self.root)!r}),Path({str(self.out)!r}),'
            + f'Path({str(self.cp)!r}),Path({str(self.work)!r}),_observe=observe)\n')
        with (self.base / 'killed.log').open('w') as log:
            child = subprocess.Popen([sys.executable, str(script)], stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 30
                while 'READY' not in (self.base / 'killed.log').read_text():
                    if child.poll() is not None or time.monotonic() > deadline:
                        self.fail((self.base / 'killed.log').read_text())
                    time.sleep(0.02)
                child.kill(); child.wait(timeout=5)
            finally:
                if child.poll() is None: child.kill(); child.wait()
        report = self.finish(resume=True)
        self.assertEqual(report['status'], 'indexed'); self.assertGreater(report['reused_edge_chunks'], 0)


if __name__ == '__main__': unittest.main()
