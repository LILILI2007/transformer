import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from modlingo.core import Project, ProjectError, TOKEN, validate_translation, unsupported_reason
from modlingo.provider import APIConfig, Translator, recover_batches


class ExpansionTests(unittest.TestCase):
    def test_full_event_scripts_are_still_blocked(self):
        for source in ['pause 100/speak Abigail Hello/end', 'none/none/farmer 0 0 2/pause 100', '$q 1/2 Question?', 'Hello#attachment object 1 1']:
            self.assertTrue(unsupported_reason(source))

    def test_common_format_tokens_and_newlines_roundtrip_through_masking(self):
        source = 'Hello {0:N2} / {{name}} ^ Bye [123] # 50%\r\nHello'
        translator = Translator(APIConfig('https://example.test', 'contract', 'key'))
        def response(messages):
            inputs = json.loads(messages[-1]['content'])['items']
            text = inputs['0']['text']
            for protected in TOKEN.findall(source):
                self.assertNotIn(protected, text)
            return json.dumps({'0': text.replace('Hello', '你好').replace('Bye', '再见')})
        with patch.object(translator, '_request', side_effect=response):
            result = translator.translate([('a', source)], {})[0][2]
        self.assertEqual(validate_translation(source, result), [])
        self.assertIn('\r\n', result)
        self.assertTrue(validate_translation(source, result.replace('{0:N2}', '{0}')))

    def test_split_export_layout_and_project_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'mod'
            source = folder / 'i18n/default'
            source.mkdir(parents=True)
            (folder / 'manifest.json').write_text('{"UniqueID":"Test.Split"}', encoding='utf-8')
            (source / 'a.json').write_text('{"a":"Hello"}', encoding='utf-8')
            project = Project.from_mod(folder)
            project.edit('a', '你好')
            project.save(Path(temp) / 'project.json')
            project = Project.load(Path(temp) / 'project.json')
            self.assertEqual(project.language_layout, 'split')
            output = Path(temp) / 'output.zip'
            project.export_zip(output)
            with zipfile.ZipFile(output) as archive:
                self.assertIn('mod/i18n/zh/ModLingo.generated.json', archive.namelist())
                self.assertNotIn('mod/i18n/zh.json', archive.namelist())

    def test_known_json_envelopes_keep_strict_id_validation(self):
        translator = Translator(APIConfig('https://example.test', 'contract', 'key'))
        responses = ['```JSON\n{"0":"你好"}\n```', '{"translations":{"0":"你好"}}', '[{"id":0,"translation":"你好"}]', '{"translations":[{"id":"0","text":"你好"}]}']
        for response in responses:
            with patch.object(translator, '_request', return_value=response):
                self.assertEqual(translator.translate([('a', 'Hello')], {})[0][2], '你好')
        for response in ['[{"id":0,"text":"你好"},{"id":"0","text":"你好"}]', '["你好"]', '[{"id":true,"text":"你好"}]', '{"translations":{"1":"你好"}}']:
            with patch.object(translator, '_request', return_value=response), self.assertRaises(ProjectError):
                translator.translate([('a', 'Hello')], {})

    def test_split_duplicate_keys_and_mixed_layout_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / 'i18n/default'
            source.mkdir(parents=True)
            (folder / 'manifest.json').write_text('{"UniqueID":"Test.Split"}', encoding='utf-8')
            (source / 'a.json').write_text('{"A":"Hello"}', encoding='utf-8')
            (source / 'b.json').write_text('{"a":"Bye"}', encoding='utf-8')
            with self.assertRaisesRegex(ProjectError, '重复键'):
                Project.from_mod(folder)
            (folder / 'i18n/zh.json').write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ProjectError, '混用'):
                Project.from_mod(folder)

    def test_recovery_call_budget_is_bounded_and_transport_not_split(self):
        items = [(str(i), 'Hello') for i in range(8)]
        translator = unittest.mock.Mock()
        translator.translate.side_effect = ProjectError('模型未返回有效 JSON')
        result = list(recover_batches(translator, items, {}))
        self.assertEqual(len(result), 8)
        self.assertEqual(translator.translate.call_count, 15)
        for error in ['API 请求失败（401）', '连接失败或超时', '服务返回重定向']:
            translator.reset_mock()
            translator.translate.side_effect = ProjectError(error)
            result = list(recover_batches(translator, items, {}))
            self.assertEqual(translator.translate.call_count, 1)
            self.assertEqual(result[0][2], error)

    def test_stop_cancels_recursive_recovery(self):
        stop = threading.Event()
        translator = unittest.mock.Mock()
        def fail(items, glossary):
            stop.set()
            raise ProjectError('模型未返回有效 JSON')
        translator.translate.side_effect = fail
        self.assertEqual(list(recover_batches(translator, [('a', 'Hi'), ('b', 'Bye')], {}, stop)), [])
        self.assertEqual(translator.translate.call_count, 1)
