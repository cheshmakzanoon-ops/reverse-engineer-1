"""Packaging checks are structural/signing gates, not Android runtime claims."""
import importlib.util
import contextlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AndroidBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("android_build", ROOT / "scripts/android_build.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def archive(self, folder, entries):
        path = Path(folder) / "sample.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name in entries:
                archive.writestr(name, b"test fixture, not an installable artifact")
        return path

    def test_apk_requires_manifest_dex_engine_and_project(self):
        entries = ["AndroidManifest.xml", "classes.dex", "lib/arm64-v8a/libgodot_android.so", "assets/project.binary"]
        with tempfile.TemporaryDirectory() as folder:
            self.module.validate_archive(self.archive(folder, entries), "apk", "arm64-v8a")
            for omitted in entries:
                with self.subTest(omitted=omitted), self.assertRaises(ValueError):
                    self.module.validate_archive(self.archive(folder, [e for e in entries if e != omitted]), "apk", "arm64-v8a")

    def test_aab_requires_bundle_layout_and_signing_records(self):
        entries = ["BundleConfig.pb", "base/manifest/AndroidManifest.xml", "base/dex/classes.dex", "base/lib/arm64-v8a/libgodot_android.so", "base/assets/project.binary", "META-INF/TEST.RSA", "META-INF/TEST.SF"]
        with tempfile.TemporaryDirectory() as folder:
            self.module.validate_archive(self.archive(folder, entries), "aab", "arm64-v8a")
            for omitted in entries:
                with self.subTest(omitted=omitted), self.assertRaises(ValueError):
                    self.module.validate_archive(self.archive(folder, [e for e in entries if e != omitted]), "aab", "arm64-v8a")

    def test_unexpected_architecture_is_rejected(self):
        entries = ["AndroidManifest.xml", "classes.dex", "lib/arm64-v8a/libgodot_android.so", "lib/x86_64/libgodot_android.so", "assets/project.binary"]
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            self.module.validate_archive(self.archive(folder, entries), "apk", "arm64-v8a")

    def test_html_download_is_not_an_apk(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.apk"
            path.write_text("<html>download failed</html>")
            with self.assertRaises(ValueError):
                self.module.validate_archive(path, "apk", "arm64-v8a")

    def test_badging_checks_package_target_and_launcher(self):
        good = "package: name='io.github.cheshmakzanoonops.kartlab.test' versionCode='3'\nsdkVersion:'24'\ntargetSdkVersion:'36'\nlaunchable-activity: name='org.godotengine.godot.GodotApp'\n"
        self.module.validate_badging(good, "io.github.cheshmakzanoonops.kartlab.test")
        for bad in [good.replace("kartlab.test", "wrong"), good.replace("'36'", "'35'"), good.replace("launchable-activity:", "no-launcher:")]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.module.validate_badging(bad, "io.github.cheshmakzanoonops.kartlab.test")

    def test_release_signing_is_never_fabricated(self):
        with self.assertRaises(ValueError):
            self.module.release_credentials({})

    def test_release_requires_existing_private_key_file(self):
        with self.assertRaises(ValueError):
            self.module.release_credentials({"GODOT_ANDROID_KEYSTORE_RELEASE_PATH": "/not/a/key", "GODOT_ANDROID_KEYSTORE_RELEASE_USER": "upload", "GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD": "private-password"})

    def test_password_redaction(self):
        self.assertEqual(self.module.redact("failure private-password", ["private-password"]), "failure [REDACTED]")

    def test_manifest_checks_bundle_api_and_launcher(self):
        good = '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="io.github.cheshmakzanoonops.kartlab.test"><uses-sdk android:minSdkVersion="24" android:targetSdkVersion="36"/><application><activity><intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter></activity></application></manifest>'
        self.module.validate_manifest(good, "io.github.cheshmakzanoonops.kartlab.test")
        for bad in [good.replace('"36"', '"35"'), good.replace("LAUNCHER", "DEFAULT"), good.replace("kartlab.test", "wrong")]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.module.validate_manifest(bad, "io.github.cheshmakzanoonops.kartlab.test")

    def test_command_logs_redact_but_validation_receives_original_output(self):
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            text = self.module.run([sys.executable, "-c", "print('private-value')"], {}, ["private-value"], 10)
        self.assertNotIn("private-value", log.getvalue())
        self.assertIn("[REDACTED]", log.getvalue())
        self.assertEqual(text.strip(), "private-value")

    def test_godot_error_is_rejected_even_with_exit_zero(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            self.module.run([sys.executable, "-c", "print('SCRIPT ERROR: broken scene')"], {}, [], 10)

    def test_presets_use_original_package_and_explicit_platform_policy(self):
        text = (ROOT / "port/export_presets.cfg").read_text()
        self.assertNotIn("com.electricsquare", text)
        self.assertNotIn("Warped Kart", text)
        self.assertEqual(text.count('gradle_build/target_sdk="36"'), 3)
        self.assertEqual(text.count('gradle_build/min_sdk="24"'), 3)
        for name in ["Android Test", "Android Bundle Test", "Android Release"]:
            self.assertIn(f'name="{name}"', text)
        self.assertNotIn('permissions/internet=true', text)
        self.assertNotIn("keystore/release_password", text)

    def test_preflight_rejects_missing_sdk_before_export(self):
        with self.assertRaises(ValueError):
            self.module.preflight(Path("/no/sdk"), Path("/no/jdk"), Path("/no/templates"))


if __name__ == "__main__":
    unittest.main()
