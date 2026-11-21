import unittest
from modlingo.acceptance import run_acceptance


class AcceptanceTests(unittest.TestCase):
    def test_owned_fixture_cold_warm_upgrade_and_restore(self):
        report=run_acceptance()
        self.assertTrue(report['passed'])
        self.assertEqual(report['paid_model_calls'],0)
        self.assertEqual(report['warm_requests'],0)
        self.assertEqual(report['same_run_backfilled_fields'],2)
        self.assertFalse(report['external_windows_verified'])
        self.assertFalse(report['game_display_verified'])


if __name__=='__main__':unittest.main()
