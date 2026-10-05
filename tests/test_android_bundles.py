"""Asset-pack and signing policies; ZIP/XML fixtures are explicitly authored."""
from __future__ import annotations
import contextlib
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import android_build as build

PACK = 'assetPackInstallTime'
ANDROID = 'http://schemas.android.com/apk/res/android'
DIST = 'http://schemas.android.com/apk/distribution'


def manifest(delivery='install-time'):
    return f'<manifest xmlns:dist="{DIST}" package="{build.PACKAGE}.test" split="{PACK}"><dist:module dist:type="asset-pack"><dist:fusing dist:include="true"/><dist:delivery><dist:{delivery}/></dist:delivery></dist:module></manifest>'


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.file=self.root/'test.aab'
        self.entries={n:b'authored test bytes, not an Android build' for n in [
            'BundleConfig.pb','base/manifest/AndroidManifest.xml','base/dex/classes.dex',
            'base/lib/arm64-v8a/libgodot_android.so',f'{PACK}/manifest/AndroidManifest.xml',
            f'{PACK}/assets.pb',f'{PACK}/assets/project.binary','META-INF/TEST.SF','META-INF/TEST.RSA']}

    def archive(self):
        with zipfile.ZipFile(self.file,'w') as z:
            for name,data in self.entries.items(): z.writestr(name,data)
        return self.file

    def test_signed_asset_pack_structure_is_accepted(self):
        self.assertEqual(build.validate_archive(self.archive(),'aab','arm64-v8a'),PACK)

    def test_all_asset_pack_metadata_and_project_are_required(self):
        for missing in [f'{PACK}/manifest/AndroidManifest.xml',f'{PACK}/assets.pb',f'{PACK}/assets/project.binary']:
            value=self.entries.pop(missing)
            with self.subTest(missing=missing),self.assertRaises(ValueError): build.validate_archive(self.archive(),'aab','arm64-v8a')
            self.entries[missing]=value

    def test_ambiguous_project_location_is_rejected(self):
        self.entries['base/assets/project.binary']=b'conflicting project'
        with self.assertRaises(ValueError): build.validate_archive(self.archive(),'aab','arm64-v8a')

    def test_empty_project_is_rejected(self):
        self.entries[f'{PACK}/assets/project.binary']=b''
        with self.assertRaises(ValueError): build.validate_archive(self.archive(),'aab','arm64-v8a')

    def test_unsigned_is_rejected_by_default_and_only_allowed_for_presign(self):
        self.entries.pop('META-INF/TEST.SF');self.entries.pop('META-INF/TEST.RSA')
        with self.assertRaisesRegex(ValueError,'signing'): build.validate_archive(self.archive(),'aab','arm64-v8a')
        self.assertEqual(build.validate_archive(self.file,'aab','arm64-v8a',require_signature=False),PACK)

    def test_nested_or_unsafe_project_paths_cannot_satisfy_gate(self):
        value=self.entries.pop(f'{PACK}/assets/project.binary')
        for name in ['../base/assets/project.binary','wrong/nested/assets/project.binary','7bad/assets/project.binary']:
            self.entries[name]=value
            with self.subTest(name=name),self.assertRaises(ValueError): build.validate_archive(self.archive(),'aab','arm64-v8a')
            self.entries.pop(name)

    def test_asset_pack_cannot_smuggle_executable_entries(self):
        for name in [f'{PACK}/dex/classes.dex',f'{PACK}/lib/x86_64/libbad.so']:
            self.entries[name]=b'not permitted'
            with self.subTest(name=name),self.assertRaises(ValueError): build.validate_archive(self.archive(),'aab','arm64-v8a')
            self.entries.pop(name)

    def test_only_install_time_unconditional_fused_pack_is_accepted(self):
        build.validate_asset_pack_manifest(manifest(),PACK,build.PACKAGE+'.test')
        for bad in [manifest('on-demand'),manifest('fast-follow'),manifest().replace('"true"','"false"'),
                    manifest().replace('asset-pack','feature'),manifest().replace('kartlab.test','wrong'),
                    manifest().replace('split="'+PACK+'"','split="other"'),
                    manifest().replace('<dist:install-time/>','<dist:install-time><dist:conditions/></dist:install-time>')]:
            with self.subTest(bad=bad),self.assertRaises(ValueError): build.validate_asset_pack_manifest(bad,PACK,build.PACKAGE+'.test')

    def test_multiple_delivery_modes_are_rejected(self):
        bad=manifest().replace('<dist:install-time/>','<dist:install-time/><dist:on-demand/>')
        with self.assertRaises(ValueError): build.validate_asset_pack_manifest(bad,PACK,build.PACKAGE+'.test')

    def test_signing_uses_chosen_identity_without_logging_passwords(self):
        self.entries.pop('META-INF/TEST.SF');self.entries.pop('META-INF/TEST.RSA')
        self.archive()
        for release in (False,True):
            prefix='GODOT_ANDROID_KEYSTORE_'+('RELEASE' if release else 'DEBUG')+'_'
            env={prefix+'PATH':str(self.root/'key'),prefix+'USER':'owner',prefix+'PASSWORD':'private-password'}
            with patch.object(build,'run',return_value='jar signed.') as run:
                build.sign_unsigned_bundle(self.file,Path('/jdk'),env,['private-password'],release=release)
            command=[str(v) for v in run.call_args.args[0]]
            self.assertIn('-storepass:env',command);self.assertIn(prefix+'PASSWORD',command)
            self.assertNotIn('private-password',command)
            self.assertEqual(command[-1],'owner')

    def test_existing_signature_is_not_silently_replaced(self):
        self.archive()
        with patch.object(build,'run') as run: build.sign_unsigned_bundle(self.file,Path('/jdk'),{},[],release=False)
        run.assert_not_called()

    def test_partial_signature_is_not_replaced(self):
        self.entries.pop('META-INF/TEST.RSA');self.archive()
        with self.assertRaises(ValueError): build.sign_unsigned_bundle(self.file,Path('/jdk'),{},[],release=False)

    @unittest.skipUnless(shutil.which('keytool') and shutil.which('jarsigner'),'JDK signing commands required')
    def test_real_jdk_signature_and_payload_preservation(self):
        java=Path(shutil.which('jarsigner')).resolve().parents[1]
        self.entries.pop('META-INF/TEST.SF');self.entries.pop('META-INF/TEST.RSA');self.archive()
        password='AuthoredFixturePassword123'
        env=dict(os.environ);prefix='GODOT_ANDROID_KEYSTORE_DEBUG_'
        env.update({prefix+'PATH':str(self.root/'test.keystore'),prefix+'USER':'fixture',prefix+'PASSWORD':password})
        with contextlib.redirect_stdout(io.StringIO()):
            build.run([java/'bin/keytool','-genkeypair','-keystore',env[prefix+'PATH'],'-alias','fixture',
                '-storepass:env',prefix+'PASSWORD','-keypass:env',prefix+'PASSWORD',
                '-keyalg','RSA','-keysize','2048','-validity','1','-dname','CN=Authored fixture'],env,[password],30)
            build.sign_unsigned_bundle(self.file,java,env,[password],release=False)
            verified=build.run([java/'bin/jarsigner','-verify',self.file],env,[password],30)
        self.assertIn('jar verified.',verified.lower())
        build.validate_archive(self.file,'aab','arm64-v8a')
        with zipfile.ZipFile(self.file) as z:
            for name,data in self.entries.items():self.assertEqual(z.read(name),data)


    def exercise_build(self, delivery):
        import argparse
        key=self.root/'fixture.keystore';key.write_bytes(b'not a signing key, signing tools mocked')
        bundletool=self.root/'bundletool.jar';bundletool.write_bytes(b'not executed')
        args=argparse.Namespace(target='test-aab',sdk=str(self.root/'sdk'),java=str(self.root/'jdk'),
            templates=str(self.root/'templates'),godot='fixture-godot',bundletool=str(bundletool),output=str(self.file))
        env={'GODOT_ANDROID_KEYSTORE_DEBUG_'+part:value for part,value in
             [('PATH',str(key)),('USER','fixture'),('PASSWORD','secret-fixture')]}
        calls=[]
        base=f'<manifest xmlns:android="{ANDROID}" package="{build.PACKAGE}.test"><uses-sdk android:minSdkVersion="24" android:targetSdkVersion="36"/><application><activity android:name="com.godot.game.GodotApp" android:exported="true"><intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter></activity></application></manifest>'
        def run(command,*args):
            command=[str(v) for v in command];calls.append(command)
            if '--version' in command:return build.ENGINE+'.stable.fixture'
            if '--export-debug' in command:self.archive();return ''
            if '-verify' in command:return 'jar verified.'
            if 'dump' in command:return base if '--module=base' in command else manifest(delivery)
            return ''
        with patch.dict(os.environ,env,clear=True),patch.object(build,'preflight'), \
             patch.object(build,'run',side_effect=run),contextlib.redirect_stdout(io.StringIO()):
            result=build.build(args)
        return result,calls

    def test_full_build_gates_asset_pack_manifest_before_success_sidecar(self):
        result,calls=self.exercise_build('install-time')
        self.assertEqual(result['project_module'],PACK)
        self.assertFalse(result['emulator_tested']);self.assertFalse(result['physical_device_tested'])
        self.assertTrue(any('--module='+PACK in call for call in calls))
        self.assertTrue(self.file.with_suffix('.aab.json').exists())

    def test_full_build_rejects_delayed_project_without_success_sidecar(self):
        with self.assertRaises(ValueError):self.exercise_build('on-demand')
        self.assertFalse(self.file.with_suffix('.aab.json').exists())


if __name__=='__main__': unittest.main()
