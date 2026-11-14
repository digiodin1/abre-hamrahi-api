from types import SimpleNamespace
import unittest

from hamrahi_backup.audit import match_summary


class VolumeRatioTests(unittest.TestCase):
    def test_only_enabled_folder_totals_determine_percentage(self):
        manager = SimpleNamespace(config={'folders': [{'id': 'a', 'enabled': True},
            {'id': 'b', 'enabled': True}, {'id': 'off', 'enabled': False}]})
        summaries = {'a': {'localBytes': 100, 'cloudBytes': 40, 'checkedAt': '2026-11-12',
            'stale': True, 'complete': False, 'verifiedBytes': 0},
            'b': {'localBytes': 300, 'cloudBytes': 60, 'checkedAt': '2026-11-12',
            'stale': False, 'complete': True, 'verifiedBytes': 300}}
        result = match_summary(manager, summaries)
        self.assertEqual(result['percent'], 25)
        self.assertEqual(result['totalBytes'], 400)
        self.assertEqual(result['cloudBytes'], 100)
        summaries['a']['cloudBytes'] = 340
        self.assertEqual(match_summary(manager, summaries)['percent'], 100)
        summaries['a']['cloudBytes'] = 440
        result = match_summary(manager, summaries)
        self.assertEqual(result['percent'], 80)
        self.assertEqual(result['extraBytes'], 100)

    def test_missing_observation_and_empty_folders(self):
        manager = SimpleNamespace(config={'folders': [{'id': 'a', 'enabled': True}]})
        self.assertIsNone(match_summary(manager, {})['percent'])
        self.assertEqual(match_summary(manager, {'a': {'localBytes': 0, 'cloudBytes': 0,
            'checkedAt': '2026-11-12'}})['percent'], 100)

    def test_changed_scope_or_unreadable_source_never_claims_known_percentage(self):
        manager = SimpleNamespace(config={'folders': [{'id': 'a', 'enabled': True}]})
        for flag in ('scopeChanged', 'sizeUnreadable'):
            result = match_summary(manager, {'a': {'localBytes': 100, 'cloudBytes': 100,
                'checkedAt': '2026-11-14', flag: True}})
            self.assertIsNone(result['percent'])
            self.assertFalse(result['known'])
            self.assertFalse(result['complete'])

    def test_percentage_uses_whole_physical_folder_not_small_eligible_subset(self):
        manager = SimpleNamespace(config={'folders': [{'id': 'a', 'enabled': True}]})
        result = match_summary(manager, {'a': {'localTotalBytes': 7057009493,
            'localBytes': 65102881, 'cloudBytes': 65129830, 'checkedAt': '2026-11-12'}})
        self.assertAlmostEqual(result['percent'], 100 * 65129830 / 7057009493)
        self.assertLess(result['percent'], 1)
        self.assertEqual(result['totalBytes'], 7057009493)
        self.assertEqual(result['excludedBytes'], 6991906612)
        self.assertEqual(result['scope'], 'physical-folder-volume')

    def test_excess_cloud_volume_reduces_similarity_instead_of_exceeding_100(self):
        manager = SimpleNamespace(config={'folders': [{'id': 'a', 'enabled': True}]})
        for local, cloud, expected in ((65085513, 65112462, 99.95861160955628),
                                       (0, 10, 0), (10, 0, 0), (100, 200, 50),
                                       (200, 100, 50), (100, 100, 100)):
            with self.subTest(local=local, cloud=cloud):
                result = match_summary(manager, {'a': {'localBytes': local, 'cloudBytes': cloud,
                    'checkedAt': '2026-11-12'}})
                self.assertAlmostEqual(result['percent'], expected, places=5)
                self.assertGreaterEqual(result['percent'], 0)
                self.assertLessEqual(result['percent'], 100)
