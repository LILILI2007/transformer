import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.check_live_api import main


class LiveCheckTests(unittest.TestCase):
    def test_no_paid_flag_never_calls_network(self):
        with patch('scripts.check_live_api.Translator') as translator, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(['--provider', 'deepseek', '--model', 'test'])
            translator.assert_not_called()

    def test_live_report_has_usage_but_no_key_or_text_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'check.json'
            with patch.dict('os.environ', {'CONTRACT_KEY': 'private-live-test-key'}), patch('scripts.check_live_api.Translator') as factory, contextlib.redirect_stdout(io.StringIO()):
                factory.return_value.translate.return_value = [('a', 'Hello', '你好')]
                factory.return_value.last_usage = {'input_tokens': 10}
                code = main(['--provider', 'openai', '--model', 'contract-model', '--key-env', 'CONTRACT_KEY', '--allow-paid', '--output', str(path)])
            self.assertEqual(code, 0)
            content = path.read_text(encoding='utf-8')
            self.assertNotIn('private-live-test-key', content)
            self.assertNotIn('你好', content)
            self.assertEqual(json.loads(content)['usage'], {'input_tokens': 10})
