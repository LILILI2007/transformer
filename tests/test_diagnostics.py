import json
import unittest
from modlingo.diagnostics import public_summary


class DiagnosticTests(unittest.TestCase):
    def test_only_known_numeric_fields_can_leave_private_report(self):
        secret = 'PRIVATE_test_value'
        report = {'mods': secret, 'api_key': secret, 'paused_reason': secret,
                  'counts': {'成功': 2, secret: 99}, 'success': [{'source': secret}],
                  'metrics': {'phase': secret, 'total': 4, 'requests': 2, 'cache_hits': secret,
                              'usage_budget': {'requests': 2, 'stopped_reason': secret,
                                               'estimated_cny': .001, 'token_limit': 2000}}}
        result = public_summary(report)
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(secret, encoded)
        self.assertEqual(result['counts'], {'成功': 2})
        self.assertEqual(result['metrics'], {'total': 4, 'requests': 2})
        self.assertEqual(result['usage']['estimated_cny'], .001)
        self.assertFalse(result['game_display_verified'])
