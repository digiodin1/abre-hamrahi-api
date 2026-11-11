import json
from pathlib import Path
import tempfile
import unittest

from hamrahi_backup.profile import load_profile, remember_profile
from hamrahi_backup import __version__
from hamrahi_backup.engine import BackupManager
from hamrahi_backup.server import PanelServer


class ProfileTests(unittest.TestCase):
    def test_public_release_matches_the_installed_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = BackupManager(Path(temporary), state_dir=Path(temporary))
            try:
                self.assertEqual(manager.public()['provider']['release'], __version__)
                server = PanelServer(manager, 0)
                try:
                    self.assertIn(__version__.encode(), server.assets['/'][0])
                    self.assertNotIn(b'{{APP_VERSION}}', server.assets['/'][0])
                finally:
                    server.server_close()
            finally:
                manager.close()

    def test_selected_profile_survives_default_launcher_and_spaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / 'default'
            chosen = Path(temporary) / 'حساب خصوصی'
            chosen.mkdir()
            remember_profile(home, chosen, 8799)
            self.assertEqual(load_profile(home), (chosen.resolve(), 8799))
            self.assertEqual(list(home.glob('*.tmp')), [])

    def test_invalid_profiles_fall_back_without_interpreting_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.assertEqual(load_profile(home), (home, 8765))
            for value in ({'directory': 'relative', 'port': 8799},
                          {'directory': str(home), 'port': True},
                          {'directory': str(home), 'port': 65536}, [], 'bad'):
                (home / 'launcher.json').write_text(json.dumps(value))
                self.assertEqual(load_profile(home), (home, 8765))
            (home / 'launcher.json').write_text('broken')
            self.assertEqual(load_profile(home), (home, 8765))
