"""Exercise launcher aliases seen in the real Godot APK, not fake build success."""
from pathlib import Path
import tempfile
import unittest
from scripts.android_manifest import apk_analyzer, validate_launcher

PREFIX = '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="io.test"><application>'
SUFFIX = '</application></manifest>'
FILTER = '<intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter>'
ACTIVITY = '<activity android:name=".Game" android:exported="false"/>'
ALIAS = '<activity-alias android:name=".Launcher" android:exported="true" android:targetActivity=".Game">'+FILTER+'</activity-alias>'


class ManifestTests(unittest.TestCase):
    def test_alias_can_launch_nonexported_target(self):
        self.assertEqual(validate_launcher(PREFIX+ACTIVITY+ALIAS+SUFFIX), 'io.test.Launcher')

    def test_regular_exported_activity(self):
        self.assertEqual(validate_launcher(PREFIX+'<activity android:name=".Game" android:exported="true">'+FILTER+'</activity>'+SUFFIX), 'io.test.Game')

    def test_exported_alias_and_target_presence_are_required(self):
        for body in (ACTIVITY+ALIAS.replace('exported="true"','exported="false"'), ALIAS,
                     ALIAS+ACTIVITY, ACTIVITY+ALIAS.replace('targetActivity=".Game"','targetActivity=".Missing"')):
            with self.subTest(body=body), self.assertRaises(ValueError):
                validate_launcher(PREFIX+body+SUFFIX)

    def test_disabled_application_alias_or_target_fails(self):
        for text in ((PREFIX+ACTIVITY+ALIAS+SUFFIX).replace('<application>','<application android:enabled="false">'),
                     PREFIX+ACTIVITY+ALIAS.replace('android:name=".Launcher"','android:name=".Launcher" android:enabled="false"')+SUFFIX,
                     PREFIX+ACTIVITY.replace('android:name=".Game"','android:name=".Game" android:enabled="false"')+ALIAS+SUFFIX):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_launcher(text)

    def test_home_replacement_is_not_a_game_launcher(self):
        text=PREFIX+ACTIVITY+ALIAS.replace('category.LAUNCHER','category.HOME')+SUFFIX
        with self.assertRaises(ValueError): validate_launcher(text)

    def test_unknown_boolean_or_permission_is_not_assumed_launchable(self):
        for addition in ('android:enabled="@bool/enabled"', 'android:permission="io.test.PRIVATE"'):
            with self.subTest(addition=addition), self.assertRaises(ValueError):
                validate_launcher(PREFIX+ACTIVITY+ALIAS.replace('android:name=".Launcher"','android:name=".Launcher" '+addition)+SUFFIX)

    def test_duplicate_component_names_fail(self):
        with self.assertRaises(ValueError): validate_launcher(PREFIX+ACTIVITY+ACTIVITY+ALIAS+SUFFIX)

    def test_main_and_launcher_must_share_intent_filter(self):
        with self.assertRaises(ValueError):
            validate_launcher(PREFIX+ACTIVITY+ALIAS.replace('/><category','/></intent-filter><intent-filter><category')+SUFFIX)

    def test_sdk_analyzer_discovery_does_not_use_unrelated_path(self):
        with tempfile.TemporaryDirectory() as folder:
            sdk=Path(folder)
            with self.assertRaises(ValueError): apk_analyzer(sdk)
            p=sdk/'cmdline-tools/16.0/bin/apkanalyzer';p.parent.mkdir(parents=True);p.touch()
            self.assertEqual(apk_analyzer(sdk),p)
            latest=sdk/'cmdline-tools/latest/bin/apkanalyzer';latest.parent.mkdir(parents=True);latest.touch()
            self.assertEqual(apk_analyzer(sdk),latest)


if __name__ == '__main__': unittest.main()
