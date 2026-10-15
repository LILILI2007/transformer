import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from modlingo.batch import check_game_stopped, install, library_lock, restore, run_batch
from modlingo.core import Project, ProjectError
from modlingo.discovery import discover_games, fingerprint, game_root, scan_mods


class FakeTranslator:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def translate(self, items, glossary):
        self.calls.extend(key for key, source in items)
        if self.failure:
            raise ProjectError(self.failure)
        return [(key, source, '中文：' + source) for key, source in items]


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.game = self.base / '游戏'
        self.mods = self.game / 'Mods'
        self.mods.mkdir(parents=True)
        (self.game / 'Stardew Valley.exe').write_bytes(b'test fixture, not executable')
        self.storage = self.base / 'storage'
        self.stop = threading.Event()
        self.mock_game = patch('modlingo.batch.check_game_stopped')
        self.mock_game.start()
        self.addCleanup(self.mock_game.stop)

    def make_mod(self, name='A', source=None, chinese=None):
        folder = self.mods / name
        (folder / 'i18n').mkdir(parents=True)
        (folder / 'manifest.json').write_text(json.dumps({'UniqueID': 'Test.' + name, 'Name': name, 'Version': '1'}), encoding='utf-8')
        (folder / 'i18n' / 'default.json').write_text(json.dumps(source or {'hello': 'Hello {{name}}$h'}), encoding='utf-8')
        if chinese is not None:
            (folder / 'i18n' / 'zh.json').write_text(json.dumps(chinese, ensure_ascii=False), encoding='utf-8')
        return folder

    def run_all(self, translator=None):
        return run_batch(scan_mods(self.mods), self.mods, translator or FakeTranslator(), self.stop, self.storage)

    def test_discovery_saved_manual_and_multiple_games(self):
        self.assertEqual(discover_games(roots=[self.game]), [self.game.resolve()])
        self.assertEqual(game_root(self.mods), self.game.resolve())
        self.assertEqual(discover_games(saved=str(self.game), roots=[]), [self.game.resolve()])
        other = self.base / 'other'
        (other / 'Mods').mkdir(parents=True)
        (other / 'Stardew Valley.exe').write_bytes(b'fixture')
        self.assertEqual(len(discover_games(roots=[self.game, other])), 2)
        with self.assertRaises(ProjectError):
            game_root(self.base)

    def test_steam_library_vdf_and_custom_install_directory(self):
        steam = self.base / 'Steam'
        apps = steam / 'steamapps'
        apps.mkdir(parents=True)
        library = self.base / 'SteamLibrary'
        actual = library / 'steamapps' / 'common' / 'Custom Farm'
        (actual / 'Mods').mkdir(parents=True)
        (actual / 'Stardew Valley.exe').write_bytes(b'fixture')
        escaped = str(library).replace('\\', '\\\\')
        (apps / 'libraryfolders.vdf').write_text('"libraryfolders" { "1" { "path" "' + escaped + '" } }', encoding='utf-8')
        (library / 'steamapps' / 'appmanifest_413150.acf').write_text('"AppState" { "installdir" "Custom Farm" }', encoding='utf-8')
        self.assertEqual(discover_games(roots=[steam]), [actual.resolve()])

    def test_nested_inventory_and_duplicate_ids(self):
        self.make_mod('group/A')
        self.make_mod('B')
        items = scan_mods(self.mods)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item.selected for item in items))
        manifest = self.mods / 'B' / 'manifest.json'
        manifest.write_text(json.dumps({'UniqueID': 'Test.group/A'}), encoding='utf-8')
        self.assertTrue(all('重复' in item.reason for item in scan_mods(self.mods)))

    def test_success_install_report_and_restore_exact_original_bytes(self):
        folder = self.make_mod(chinese={'extra': '保留额外键'})
        target = folder / 'i18n' / 'zh.json'
        before = target.read_bytes()
        sources = {p: p.read_bytes() for p in [folder / 'manifest.json', folder / 'i18n' / 'default.json']}
        path, report = self.run_all()
        result = report['success'][0]
        self.assertEqual(result['installed'], 1)
        self.assertIn('中文', target.read_text(encoding='utf-8'))
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['extra'], '保留额外键')
        self.assertTrue(path.with_suffix('.txt').exists())
        self.assertNotIn('api_key', path.read_text(encoding='utf-8'))
        restore(result['receipt'])
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(sources, {p: p.read_bytes() for p in sources})

    def test_restore_removes_only_new_file(self):
        folder = self.make_mod()
        _, report = self.run_all()
        restore(report['success'][0]['receipt'])
        self.assertFalse((folder / 'i18n' / 'zh.json').exists())

    def test_restore_preserves_later_player_edits(self):
        folder = self.make_mod()
        _, report = self.run_all()
        target = folder / 'i18n' / 'zh.json'
        target.write_text('{"hello":"玩家修改"}', encoding='utf-8')
        with self.assertRaisesRegex(ProjectError, '后续修改'):
            restore(report['success'][0]['receipt'])
        self.assertIn('玩家修改', target.read_text(encoding='utf-8'))

    def test_restore_rejects_corrupt_backup(self):
        self.make_mod(chinese={'extra': '中文'})
        _, report = self.run_all()
        receipt = Path(report['success'][0]['receipt'])
        (receipt.parent / 'original-zh.bin').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ProjectError, '备份校验'):
            restore(receipt)

    def test_unsupported_and_bad_mod_do_not_hide_success(self):
        self.make_mod('Good')
        self.make_mod('Partial', {'hello': 'Hello', 'event': '$q 1/2 Question'})
        bad = self.make_mod('Bad')
        (bad / 'i18n' / 'default.json').unlink()
        _, report = self.run_all()
        self.assertEqual(len(report['success']), 1)
        self.assertEqual(len(report['failure']), 2)
        partial = next(r for r in report['failure'] if r['name'] == 'Partial')
        self.assertEqual(partial['status'], '部分完成')
        self.assertIn('复杂', partial['reason'])
        self.assertTrue(partial['receipt'])

    def test_changed_input_rejected_before_network(self):
        folder = self.make_mod()
        items = scan_mods(self.mods)
        (folder / 'i18n' / 'default.json').write_text('{"hello":"Updated"}', encoding='utf-8')
        translator = FakeTranslator()
        _, report = run_batch(items, self.mods, translator, self.stop, self.storage)
        self.assertEqual(translator.calls, [])
        self.assertIn('发生变化', report['failure'][0]['reason'])
        self.assertFalse((folder / 'i18n' / 'zh.json').exists())

    def test_changed_target_during_network_is_not_overwritten(self):
        folder = self.make_mod(chinese={'extra': '原文件'})
        class RacingTranslator(FakeTranslator):
            def translate(inner, items, glossary):
                (folder / 'i18n' / 'zh.json').write_text('{"hello":"后来修改"}', encoding='utf-8')
                return super().translate(items, glossary)
        _, report = self.run_all(RacingTranslator())
        self.assertIn('发生变化', report['failure'][0]['reason'])
        self.assertIn('后来修改', (folder / 'i18n' / 'zh.json').read_text(encoding='utf-8'))

    def test_stop_accounts_for_every_selection_without_installing(self):
        self.make_mod('A')
        self.make_mod('B')
        self.stop.set()
        translator = FakeTranslator()
        _, report = self.run_all(translator)
        self.assertEqual(len(report['failure']), 2)
        self.assertEqual(translator.calls, [])

    def test_bad_key_stops_further_paid_requests(self):
        self.make_mod('A')
        self.make_mod('B')
        for status in (400, 401):
            with self.subTest(status=status):
                translator = FakeTranslator(f'API 请求失败（{status}）：配置无效')
                _, report = self.run_all(translator)
                self.assertEqual(len(translator.calls), 1)
                self.assertEqual(len(report['failure']), 2)
                self.assertIn('剩余', report['failure'][1]['reason'])

    def test_failed_request_preserves_original_and_retry_uses_saved_batches(self):
        source = {str(i): 'Hello ' + str(i) for i in range(10)}
        folder = self.make_mod(source=source, chinese={'extra': '原文件'})
        original = (folder / 'i18n' / 'zh.json').read_bytes()
        class FailSecond(FakeTranslator):
            count = 0
            def translate(inner, items, glossary):
                inner.count += 1
                if inner.count == 2:
                    raise ProjectError('模拟第二批失败')
                return super().translate(items, glossary)
        _, report = self.run_all(FailSecond())
        self.assertEqual(len(report['failure']), 1)
        self.assertEqual((folder / 'i18n' / 'zh.json').read_bytes(), original)
        translator = FakeTranslator()
        _, report = self.run_all(translator)
        self.assertEqual(len(report['success']), 1)
        self.assertEqual(len(translator.calls), 2)

    def test_update_reuses_unchanged_and_accepts_changed_candidate(self):
        folder = self.make_mod(source={'a': 'First', 'b': 'Second'})
        self.run_all()
        (folder / 'i18n' / 'default.json').write_text('{"a":"First changed","b":"Second"}', encoding='utf-8')
        translator = FakeTranslator()
        _, report = self.run_all(translator)
        self.assertEqual(translator.calls, ['a'])
        self.assertEqual(len(report['success']), 1)

    def test_failed_update_retry_keeps_new_candidates_over_old_installed_chinese(self):
        folder = self.make_mod(source={str(i): 'Old ' + str(i) for i in range(10)})
        self.run_all()
        (folder / 'i18n' / 'default.json').write_text(json.dumps({str(i): 'New ' + str(i) for i in range(10)}), encoding='utf-8')
        class FailSecond(FakeTranslator):
            count = 0
            def translate(inner, items, glossary):
                inner.count += 1
                if inner.count == 2:
                    raise ProjectError('第二批失败')
                return super().translate(items, glossary)
        self.run_all(FailSecond())
        translator = FakeTranslator()
        _, report = self.run_all(translator)
        self.assertEqual(len(translator.calls), 2)
        self.assertEqual(len(report['success']), 1)
        values = json.loads((folder / 'i18n' / 'zh.json').read_text(encoding='utf-8'))
        self.assertTrue(all('New' in value for value in values.values()))

    def test_existing_chinese_and_player_edits_are_not_retranslated(self):
        folder = self.make_mod(chinese={'hello': '你好 {{name}}$h'})
        translator = FakeTranslator()
        self.run_all(translator)
        self.assertEqual(translator.calls, [])
        target = folder / 'i18n' / 'zh.json'
        target.write_text('{"hello":"玩家改写 {{name}}$h"}', encoding='utf-8')
        self.run_all(translator)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['hello'], '玩家改写 {{name}}$h')

    def test_suspicious_english_blocks_install_and_can_retry(self):
        folder = self.make_mod()
        class Echo(FakeTranslator):
            def translate(inner, items, glossary):
                return [(k, s, s) for k, s in items]
        _, report = self.run_all(Echo())
        self.assertEqual(len(report['failure']), 1)
        self.assertFalse((folder / 'i18n' / 'zh.json').exists())
        _, report = self.run_all()
        self.assertEqual(len(report['success']), 1)

    def test_library_lock_prevents_two_writers_and_releases(self):
        with library_lock(self.mods, self.storage):
            with self.assertRaises(ProjectError):
                with library_lock(self.mods, self.storage):
                    pass
        with library_lock(self.mods, self.storage):
            pass

    def test_running_game_blocks_installation(self):
        self.make_mod()
        with patch('modlingo.batch.check_game_stopped', side_effect=ProjectError('请先退出游戏')):
            with self.assertRaisesRegex(ProjectError, '退出游戏'):
                self.run_all()
        self.assertFalse(list(self.mods.rglob('zh.json')))

    def test_target_write_failure_preserves_original_and_backup(self):
        folder = self.make_mod(chinese={'extra': '原始文件'})
        target = folder / 'i18n' / 'zh.json'
        original = target.read_bytes()
        from modlingo.batch import atomic_write
        def fail_target(path, content):
            if Path(path).resolve() == target.resolve():
                raise PermissionError('test denied')
            return atomic_write(path, content)
        with patch('modlingo.batch.atomic_write', side_effect=fail_target):
            _, report = self.run_all()
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(len(report['failure']), 1)
        self.assertEqual(next((self.storage / 'backups').rglob('original-zh.bin')).read_bytes(), original)

    def test_symlink_mod_is_not_followed(self):
        outside = self.base / 'outside'
        outside.mkdir()
        link = self.mods / 'linked'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink permission unavailable')
        items = scan_mods(self.mods)
        self.assertEqual(len(items), 1)
        self.assertIn('链接', items[0].reason)
