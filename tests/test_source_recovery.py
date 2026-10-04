"""Source-to-inventory gates. All generated inputs are explicitly test fixtures."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import recover_source as r
from tools.content_pipeline import ContentError, canonical


class SourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def app(self, name='Test.app'):
        content = self.root / name / 'Contents'
        metadata = content / 'Resources/Data/il2cpp_data/Metadata/global-metadata.dat'
        metadata.parent.mkdir(parents=True)
        metadata.write_bytes(bytes.fromhex('af1bb1fa1f000000') + bytes(128))
        binary = content / 'Frameworks/GameAssembly.dylib'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(bytes.fromhex('cafebabe') + bytes(128))
        (content / 'Resources/Data/globalgamemanagers').write_bytes(b'test-fixture')
        return content

    def test_drive_id_is_not_a_shell_command_or_url(self):
        self.assertEqual(r.drive_id('abc_DEF-123'), 'abc_DEF-123')
        for value in ('', '../escape', 'x;id', 'https://drive.google.com/file/d/x', 'x\ny', 'x' * 201):
            with self.subTest(value=value), self.assertRaises(ValueError):
                r.drive_id(value)

    def test_find_exact_application(self):
        content = self.app()
        self.assertEqual(r.find_application(self.root), content)

    def test_find_missing_or_ambiguous_application_fails(self):
        with self.assertRaises(ContentError):
            r.find_application(self.root)
        self.app('A.app'); self.app('B.app')
        with self.assertRaises(ContentError):
            r.find_application(self.root)

    def test_metadata_header_is_verified(self):
        content = self.app()
        report = r.binary_metadata(content)
        self.assertEqual(report['metadata_version'], 31)
        self.assertEqual(report['native_format'], 'universal-big-endian')
        self.assertFalse(report['original_source_recovered'])

    def test_metadata_or_binary_corruption_fails(self):
        content = self.app()
        metadata = content / 'Resources/Data/il2cpp_data/Metadata/global-metadata.dat'
        metadata.write_bytes(b'not metadata')
        with self.assertRaises(ContentError):
            r.binary_metadata(content)
        metadata.write_bytes(bytes.fromhex('af1bb1fa1f000000'))
        (content / 'Frameworks/GameAssembly.dylib').write_bytes(b'not Mach-O')
        with self.assertRaises(ContentError):
            r.binary_metadata(content)

    def test_file_manifest_hashes_bytes_and_excludes_sidecars(self):
        (self.root / 'file.bundle').write_bytes(b'real-test-fixture')
        (self.root / 'file.bundle:com.apple.macl').write_bytes(b'not a bundle')
        (self.root / '._file.bundle').write_bytes(b'sidecar')
        manifest = r.extracted_manifest(self.root)
        self.assertEqual([f['path'] for f in manifest], ['file.bundle'])
        self.assertEqual(manifest[0]['bytes'], 17)
        self.assertEqual(len(manifest[0]['sha256']), 64)

    def test_symlink_cannot_enter_manifest(self):
        (self.root / 'real').write_bytes(b'fixture')
        (self.root / 'link').symlink_to(self.root / 'real')
        with self.assertRaises(ContentError):
            r.extracted_manifest(self.root)

    def test_empty_regular_files_are_counted_not_silently_dropped(self):
        (self.root / 'empty').touch()
        manifest = r.extracted_manifest(self.root)
        self.assertEqual(manifest[0]['bytes'], 0)

    def test_report_schema_cannot_embed_object_payloads(self):
        catalog = {'status': 'incomplete', 'source_status': 'synthetic-adapter-fixture',
                   'counts': {'objects': 1, 'errors': 1, 'types': {'Mesh': 1}},
                   'errors': [], 'objects': [{'id': 'a', 'file': 'a.bundle/CAB',
                   'path_id': '1', 'type': 'Mesh', 'name': 'hank_mesh',
                   'errors': [{'field': '/x', 'reason': 'missing reference'}],
                   'artifacts': [{'role': 'raw-object', 'path': 'secret.bin'}],
                   'payload': 'DO NOT SHARE', 'conversion': 'raw-only'}]}
        summary = r.inventory_summary(catalog)
        self.assertFalse(summary['complete_game'])
        self.assertEqual(summary['status'], 'incomplete')
        self.assertEqual(summary['error_groups'][0]['count'], 1)
        self.assertNotIn('DO NOT SHARE', json.dumps(summary))
        self.assertNotIn('secret.bin', json.dumps(summary))
        self.assertEqual(summary['candidates'][0]['name'], 'hank_mesh')

    def test_successful_inventory_is_not_an_import_or_source_claim(self):
        summary = r.inventory_summary({'status': 'indexed', 'counts': {}, 'errors': [], 'objects': []})
        for field in ('godot_imported', 'original_source_recovered', 'complete_game'):
            self.assertIs(summary[field], False)

    def test_command_failure_is_not_ignored(self):
        with self.assertRaises(ContentError):
            r.run([r.sys.executable, '-c', 'raise SystemExit(3)'], self.root / 'failure.log', timeout=10)

    def test_command_timeout_preserves_log(self):
        with self.assertRaises(ContentError):
            r.run([r.sys.executable, '-c', 'import time; print("fixture", flush=True); time.sleep(5)'],
                  self.root / 'timeout.log', timeout=1.0)
        self.assertIn('fixture', (self.root / 'timeout.log').read_text())

    def test_unverified_dmg_never_reaches_extractor(self):
        bad = self.root / 'bad.dmg'; bad.write_bytes(b'fixture-not-the-game')
        with patch.object(r, 'run') as command:
            rc = r.main(['--dmg', str(bad), '--workspace', str(self.root / 'work'),
                         '--reports', str(self.root / 'reports')])
        self.assertEqual(rc, 1)
        command.assert_not_called()
        report = json.loads((self.root / 'reports/recovery.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['source_verified'])
        self.assertFalse(report['extracted'])

    def test_existing_report_is_never_overwritten(self):
        reports = self.root / 'reports'; reports.mkdir()
        (reports / 'recovery.json').write_text('preserve-me')
        rc = r.main(['--dmg', str(self.root / 'missing'), '--workspace', str(self.root / 'work'),
                     '--reports', str(reports)])
        self.assertEqual(rc, 1)
        self.assertEqual((reports / 'recovery.json').read_text(), 'preserve-me')

    def test_reports_and_media_workspace_cannot_overlap(self):
        with self.assertRaises(ContentError):
            r.prepare_directories(self.root / 'work', self.root / 'work/reports')
        with self.assertRaises(ContentError):
            r.prepare_directories(self.root / 'reports/work', self.root / 'reports')

    def test_extraction_manifest_deterministic(self):
        self.app()
        self.assertEqual(canonical(r.extracted_manifest(self.root)), canonical(r.extracted_manifest(self.root)))

    def test_restore_options_must_be_paired_before_any_source_read(self):
        for option in ('--restore-checkpoints', '--checkpoint-key'):
            workspace = self.root / ('work-' + option[2:])
            reports = self.root / ('reports-' + option[2:])
            with self.subTest(option=option), patch.object(r, 'verify_source') as verify:
                rc = r.main(['--dmg', str(self.root/'missing'), '--workspace', str(workspace),
                             '--reports', str(reports), option, str(self.root/'missing-key-or-archive')])
                self.assertEqual(rc, 1)
                verify.assert_not_called()
                self.assertFalse(workspace.exists())

    def test_restored_checkpoint_path_reaches_inventory_with_resume(self):
        content = self.app()
        workspace = self.root/'new-work'
        reports = self.root/'new-reports'
        key = self.root/'key.pem'
        key.write_bytes(b'fixture-key-never-published')
        archive = self.root/'encrypted'
        observed = []
        def stop_inventory(command, *_args, **_kwargs):
            observed.append(command)
            raise ContentError('fixture stops before inventory')
        with patch.object(r, 'verify_source', return_value={'bytes': 8, 'sha256': 'fixture'}), \
             patch.object(r, 'extract_original'), patch.object(r, 'find_application', return_value=content), \
             patch.object(r, 'run', side_effect=stop_inventory), \
             patch('tools.content_checkpoints.restore_checkpoints') as restore, \
             patch('tools.content_unity.source_files', return_value=['fixture-source']):
            rc = r.main(['--dmg', str(self.root/'fixture.dmg'), '--workspace', str(workspace),
                         '--reports', str(reports), '--restore-checkpoints', str(archive),
                         '--checkpoint-key', str(key)])
        self.assertEqual(rc, 1)
        restore.assert_called_once_with(archive, b'fixture-key-never-published', workspace/'content-checkpoints',
                                        expected_sources=['fixture-source'])
        self.assertEqual(len(observed), 1)
        self.assertIn('--resume', observed[0])
        self.assertIn(str(workspace/'content-checkpoints'), observed[0])
        self.assertNotIn('fixture-key-never-published', (reports/'recovery.json').read_text())


if __name__ == '__main__':
    unittest.main()
