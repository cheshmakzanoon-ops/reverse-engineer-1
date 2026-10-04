"""Regressions for failures observed in the real source and Android CI runs."""
import configparser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import recover_source as source
from tools.content_pipeline import ContentError


class PlatformRepairTests(unittest.TestCase):
    def test_installer_shortcut_is_excluded_without_allowing_unsafe_links(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(source, 'run') as run:
            root = Path(folder)
            source.extract_original(root / 'game.dmg', root / 'extracted', root, '7zz')
        command = run.call_args.args[0]
        self.assertIn('-x!Warped Kart Racers/Applications', command)
        self.assertNotIn('-snld', command)
        self.assertEqual(command[-1], str(root / 'game.dmg'))
        self.assertEqual([a for a in command if a.startswith('-x')], ['-x!Warped Kart Racers/Applications'])

    def test_other_extraction_errors_still_fail(self):
        with patch.object(source, 'run', side_effect=ContentError('bad archive')):
            with self.assertRaisesRegex(ContentError, 'bad archive'):
                source.extract_original(Path('game.dmg'), Path('out'), Path('reports'), '7zz')

    def test_all_android_presets_are_apps_not_home_screen_replacements(self):
        config = configparser.ConfigParser()
        config.read(Path(__file__).resolve().parents[1] / 'port/export_presets.cfg')
        options = [config[s] for s in config.sections() if s.endswith('.options')]
        self.assertEqual(len(options), 3)
        for option in options:
            self.assertFalse(option.getboolean('package/show_as_launcher_app'))
            self.assertTrue(option.getboolean('package/show_in_app_library'))


if __name__ == '__main__':
    unittest.main()
