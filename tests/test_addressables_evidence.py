"""Source-gate wiring tests are authored, never original-catalog evidence."""
from __future__ import annotations
import contextlib
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_addressables as gate
from tests.test_content_addressables import make_catalog, plan
from tools.content_pipeline import ContentError, canonical


class EvidenceTests(unittest.TestCase):
    def test_source_pins_are_exact_recorded_metadata(self):
        self.assertEqual(gate.CATALOG_BYTES, 1505032)
        self.assertEqual(gate.CATALOG_SHA256, '742d72cb89e182f97c3656fef22eac67a863da6cfd5a2a8940a5822a57f6b9be')
        self.assertEqual(gate.EXTRACTED_MANIFEST_SHA256, '89ed0deffe5bad3a93e38b769037fe943071185c748e65fa9dd814291f0db25b')

    def test_layout_and_wrong_source_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ContentError): gate.inspect_original(Path(d), {})
            data = Path(d)/'Contents/Resources/Data'; data.mkdir(parents=True)
            (data/'not-original').write_bytes(b'authored')
            with self.assertRaises(ContentError): gate.inspect_original(data, {})

    def test_source_manifest_before_catalog_reader(self):
        with tempfile.TemporaryDirectory() as d, patch.object(gate.CompactCatalog, 'load') as load:
            data=Path(d)/'Contents/Resources/Data'; data.mkdir(parents=True)
            (data/'fixture').write_bytes(b'fixture')
            with self.assertRaises(ContentError): gate.inspect_original(data, {})
            load.assert_not_called()

    def test_failure_report_never_claims_recovery_or_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); data=root/'bad-source'; data.mkdir(); report=root/'result.json'
            with contextlib.redirect_stdout(io.StringIO()):
                code=gate.main(['--data',str(data),'--report',str(report)])
            self.assertEqual(code, 1)
            from tools.content_addressables import strict_json
            result=strict_json(report.read_bytes())
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn(str(root), report.read_text())
            for key in ('original_dmg_verified','object_bindings_complete','godot_imported','original_source_recovered','original_media_complete'):
                self.assertFalse(result[key])
            before=report.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                gate.main(['--data',str(data),'--report',str(report)])
            self.assertEqual(before,report.read_bytes())

    def test_synthetic_success_exercises_report_without_claiming_production_test(self):
        with tempfile.TemporaryDirectory() as d:
            data=Path(d)/'Contents/Resources/Data'; data.mkdir(parents=True)
            path=data/gate.CATALOG_PATH; path.parent.mkdir(parents=True); path.write_bytes(canonical(make_catalog()))
            for name in ('driver','shared'):
                bundle=path.parent/'StandaloneOSX'/f'{name}.bundle'
                bundle.parent.mkdir(exist_ok=True); bundle.write_bytes(b'fixture-'+name.encode())
            manifest=gate.extracted_manifest(data.parent.parent)
            digest=hashlib.sha256(canonical(manifest)).hexdigest()
            with patch.object(gate,'EXTRACTED_MANIFEST_SHA256',digest), \
                    patch.object(gate,'CATALOG_BYTES',path.stat().st_size), \
                    patch.object(gate,'CATALOG_SHA256',hashlib.sha256(path.read_bytes()).hexdigest()), \
                    patch.object(gate,'make_requirements',return_value=plan()):
                report={}
                gate.inspect_original(data,report)
            self.assertEqual(report['status'],'original-catalog-locations-verified')
            self.assertEqual(report['location_plan']['verified_bundle_files'],2)
            # This patched fixture remains ONLY in tests, not a CLI production switch.
            self.assertFalse(report['location_plan']['object_bindings_complete'])

    def test_workflow_artifact_is_only_metadata(self):
        workflow=(gate.ROOT/'.github/workflows/addressables-evidence.yml').read_text()
        self.assertIn('path: verification/addressables.json',workflow)
        self.assertNotIn('contents: write',workflow)
        self.assertIn('timeout-minutes: 15',workflow)
        self.assertNotIn('continue-on-error',workflow)


if __name__ == '__main__': unittest.main()
