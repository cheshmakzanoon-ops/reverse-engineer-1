"""Installed-package launcher resolution; no fixture claims an emulator run."""
import unittest
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch
from scripts import android_smoke as smoke

PACKAGE = 'io.github.cheshmakzanoonops.kartlab.test'


class LauncherResolutionTests(unittest.TestCase):
    def test_resolves_exported_alias_instead_of_assuming_engine_activity(self):
        value = PACKAGE + '/com.godot.game.GodotAppLauncher'
        self.assertEqual(smoke.resolve_launcher(value + '\n', PACKAGE), value)

    def test_accepts_short_component_with_package_manager_metadata(self):
        value = PACKAGE + '/.Launcher'
        self.assertEqual(smoke.resolve_launcher('priority=0 preferredOrder=0 match=0x108000\n' + value, PACKAGE), value)

    def test_missing_ambiguous_or_foreign_launcher_fails(self):
        for value in ('', 'No activity found', 'other.package/.Launcher',
                      PACKAGE + '/.A\n' + PACKAGE + '/.B'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                smoke.resolve_launcher(value, PACKAGE)

    def test_shell_fragments_and_invalid_component_are_rejected(self):
        for suffix in ('/.A;id', '/.A x', '/../../evil', '/@string/name', '/1Bad'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                smoke.resolve_launcher(PACKAGE + suffix, PACKAGE)

    def test_prefix_package_match_is_not_enough(self):
        with self.assertRaises(ValueError):
            smoke.resolve_launcher(PACKAGE + '.evil/.Launcher', PACKAGE)

    def test_smoke_uses_resolved_alias_for_all_six_start_commands(self):
        component = PACKAGE + '/com.godot.game.GodotAppLauncher'
        seen = []
        def command(args, **kwargs):
            seen.append(args)
            if 'screencap' in args:
                result = b'\x89PNG\r\n\x1a\nfixture'
            elif 'resolve-activity' in args:
                result = component + '\n'
            elif 'install' in args:
                result = 'Success\n'
            elif 'pidof' in args:
                result = '1234\n'
            elif 'getprop' in args:
                result = '1\n' if args[-1] == 'ro.kernel.qemu' else 'fixture\n'
            elif 'start' in args:
                self.assertEqual(args[-1], component)
                result = 'Status: ok\n'
            else:
                result = ''
            return subprocess.CompletedProcess(args, 0, stdout=result)
        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / 'fixture.apk'
            apk.write_bytes(b'authored mock input, not an APK')
            with patch.object(smoke.subprocess, 'run', side_effect=command), patch.object(smoke.time, 'sleep'):
                smoke.smoke('adb', 'emulator-5554', apk, Path(folder) / 'out')
        starts = [args for args in seen if 'start' in args]
        self.assertEqual(len(starts), 6)


if __name__ == '__main__':
    unittest.main()
