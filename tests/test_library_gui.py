import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from modlingo.core import ProjectError
from modlingo.discovery import ModItem


@unittest.skipUnless(os.name == 'nt' or os.environ.get('DISPLAY'), 'GUI tests need a display')
class LibraryGUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from modlingo.library_gui import LibraryApp
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name})
        self.environment.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = LibraryApp(self.root, auto=False)
        self.root.update_idletasks()

    def tearDown(self):
        for callback in self.root.tk.call('after', 'info'):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.environment.stop()
        self.temp.cleanup()

    def pump(self):
        deadline = time.monotonic() + 5
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.app.busy)

    def test_automatic_detection_populates_default_checked_inventory(self):
        game = Path(self.temp.name) / 'Game'
        item = ModItem(game / 'Mods' / 'A', '示例')
        with patch('modlingo.library_gui.call_in_process', return_value=([game],game,[item])):
            self.app.detect()
            self.pump()
        self.assertEqual(self.app.mods, game / 'Mods')
        self.assertEqual(self.app.tree.set('0', 'pick'), '☑')
        self.app.toggle_index('0')
        self.assertFalse(item.selected)
        self.app.select_all(True)
        self.assertTrue(item.selected)

    def test_multiple_games_require_explicit_choice(self):
        with patch('modlingo.library_gui.call_in_process', return_value=([Path('A'),Path('B')],None,[])):
            self.app.detect()
            self.pump()
        self.assertIsNone(self.app.mods)
        self.assertIn('多份', self.app.status.get())

    def test_results_and_report_reach_ui(self):
        game = Path(self.temp.name) / 'Game'
        item = ModItem(game / 'Mods' / 'A', '示例')
        self.app.show_items(game, [item])
        result = {'folder': str(item.folder), 'name': '示例', 'status': '失败', 'reason': '没有标准语言文件'}
        self.app.set_busy(True)
        self.app.events.put(('result', result))
        self.app.events.put(('report', (Path(self.temp.name) / 'report.json', {'success': [], 'failure': [result]})))
        self.app.events.put(('done', None))
        self.app.poll()
        self.assertEqual(self.app.tree.set('0', 'reason'), '没有标准语言文件')
        self.assertFalse(self.app.busy)
        self.assertIn('失败 1 个', self.app.summary.get())

    def test_close_while_busy_requests_stop_and_keeps_window(self):
        self.app.set_busy(True)
        self.app.close()
        self.assertTrue(self.app.stop.is_set())
        self.assertTrue(self.root.winfo_exists())

    def test_search_preserves_selection_and_all_selects_hidden_mods(self):
        game = Path(self.temp.name) / 'Game'
        items = [ModItem(game / 'Mods' / 'A', 'Apple'), ModItem(game / 'Mods' / 'B', 'Berry')]
        self.app.show_items(game, items)
        self.app.search.set('apple')
        self.assertEqual(self.app.tree.get_children(), ('0',))
        self.assertTrue(items[1].selected)
        self.app.select_all(False)
        self.assertFalse(any(item.selected for item in items))
        self.assertEqual(self.app.metrics['selected'].get(), '0')
        self.app.search.set('')
        self.assertEqual(self.app.tree.get_children(), ('0', '1'))

    def test_rescan_clears_detached_rows_from_previous_inventory(self):
        game = Path(self.temp.name) / 'Game'
        self.app.show_items(game, [ModItem(game / 'Mods' / str(i), str(i)) for i in range(3)])
        self.app.search.set('0')
        self.assertTrue(self.app.tree.exists('2'))
        self.app.show_items(game, [ModItem(game / 'Mods' / 'new', 'New')])
        self.app.search.set('')
        self.assertEqual(self.app.tree.get_children(), ('0',))
        self.assertFalse(self.app.tree.exists('2'))

    def test_filtered_result_updates_counts_and_shows_complete_reason(self):
        game = Path(self.temp.name) / 'Game'
        item = ModItem(game / 'Mods' / 'A', 'Apple')
        self.app.show_items(game, [item])
        self.app.view.set('需要关注')
        self.assertEqual(self.app.tree.get_children(), ())
        self.app.events.put(('result', {'folder': str(item.folder), 'name': item.name, 'status': '失败', 'reason': '完整失败原因'}))
        self.app.poll()
        self.assertEqual(self.app.tree.get_children(), ('0',))
        self.assertEqual(self.app.metrics['attention'].get(), '1')
        self.app.tree.selection_set('0')
        self.app.select_details()
        self.assertIn('完整失败原因', self.app.row_details.get())

    def test_minimum_window_keeps_primary_action_visible(self):
        self.root.deiconify()
        width, height = self.root.minsize()
        self.root.geometry('%dx%d' % (width, height))
        self.root.update()
        button_bottom = self.app.start_button.winfo_rooty() + self.app.start_button.winfo_height()
        window_bottom = self.root.winfo_rooty() + self.root.winfo_height()
        self.assertLessEqual(button_bottom, window_bottom)
        self.assertGreater(self.app.tree.winfo_height(), 60)

    def test_provider_switch_clears_key_and_sets_native_protocol(self):
        from modlingo.provider import PRESETS, PROTOCOLS
        self.app.config.api_key = 'private-ui-test-key'
        self.app.settings()
        dialog = self.app.api_dialog
        dialog.provider.set(PRESETS['claude'].label)
        dialog.choose_provider()
        self.assertEqual(dialog.api_key.get(), '')
        self.assertEqual(dialog.model.get(), '')
        self.assertEqual(dialog.protocol.get(), PROTOCOLS['anthropic'])
        self.assertEqual(dialog.base_url.get(), 'https://api.anthropic.com/v1')
        dialog.model.set('contract-model')
        dialog.api_key.set('new-test-key')
        dialog.workspace.set('wrkspc_contract')
        dialog.save()
        self.assertEqual(self.app.config.protocol, 'anthropic')
        self.assertEqual(self.app.config.workspace_id, 'wrkspc_contract')

    def test_custom_protocol_and_qwen_thinking_defaults(self):
        from modlingo.provider import PRESETS, PROTOCOLS
        self.app.settings()
        dialog = self.app.api_dialog
        dialog.provider.set(PRESETS['qwen'].label)
        dialog.choose_provider()
        self.assertTrue(dialog.disable_thinking.get())
        dialog.provider.set(PRESETS['custom'].label)
        dialog.choose_provider()
        self.assertFalse(dialog.disable_thinking.get())
        dialog.base_url.set('http://127.0.0.1:8080/v1')
        dialog.model.set('local')
        dialog.protocol.set(PROTOCOLS['responses'])
        dialog.run_requests.set('3')
        dialog.run_tokens.set('12000')
        dialog.input_price.set('0.5')
        dialog.output_price.set('1')
        self.assertEqual(dialog.candidate().endpoint(), 'http://127.0.0.1:8080/v1/responses')
        self.assertEqual(dialog.candidate().run_request_limit, 3)
        self.assertEqual(dialog.candidate().run_token_limit, 12000)
        self.assertEqual(dialog.candidate().input_price_cny, .5)
        dialog.provider.set(PRESETS['qwen'].label)
        dialog.choose_provider()
        self.assertEqual(dialog.input_price.get(), '')
        self.assertEqual(dialog.output_price.get(), '')

    def test_single_editor_uses_same_provider_configuration(self):
        from modlingo.gui import App
        from modlingo.provider import preset_config
        window = __import__('tkinter').Toplevel(self.root)
        editor = App(window)
        editor.config = preset_config('openai', 'contract-model', 'private-test-key')
        editor.settings()
        self.assertEqual(editor.api_dialog.candidate().protocol, 'responses')
        editor.api_dialog.save()
        self.assertEqual(editor.config.provider, 'openai')
        editor.close()

    def test_unverified_config_cannot_start_even_when_called_directly(self):
        from modlingo.provider import preset_config
        self.app.config = preset_config('deepseek', 'contract', 'test-key')
        self.assertTrue(self.app.start_button.instate(['disabled']))
        with patch('modlingo.api_verification.messagebox.showwarning') as warning, patch('modlingo.library_gui.run_batch') as batch:
            self.app.start()
            warning.assert_called_once()
            batch.assert_not_called()

    def test_success_unlocks_and_saving_configuration_locks_again(self):
        from dataclasses import replace
        from modlingo.provider import preset_config
        self.app.config = preset_config('deepseek', 'contract', 'test-key')
        with patch('modlingo.api_verification.messagebox.askyesno', return_value=True), patch('modlingo.api_verification.messagebox.showinfo'), patch('modlingo.api_verification.verify_connection') as factory:
            factory.return_value = [('connection-test', 'Hello', '你好')]
            self.app.test_connection()
            self.assertTrue(self.app.start_button.instate(['disabled']))
            self.pump()
        self.assertTrue(self.app.api_ready())
        self.assertIn('验证成功', self.app.api_status.get())
        self.assertFalse(self.app.start_button.instate(['disabled']))
        for field, value in [('model', 'changed-model'), ('api_key', 'changed-key'), ('base_url', 'https://another.example/v1'), ('timeout', 180), ('max_output_tokens', 4096), ('disable_thinking', not self.app.config.disable_thinking)]:
            original = replace(self.app.config)
            setattr(self.app.config, field, value)
            self.assertFalse(self.app.api_ready(), field)
            self.app.config = original
        self.app.settings()
        self.app.api_dialog.save()
        self.assertFalse(self.app.api_ready())
        self.assertTrue(self.app.start_button.instate(['disabled']))

    def test_failed_or_non_chinese_verification_keeps_translation_locked(self):
        from modlingo.provider import preset_config
        self.app.config = preset_config('deepseek', 'contract', 'test-key')
        for error in [ProjectError('API 请求失败（401）：密钥无效'), RuntimeError('private-internal-detail'), None]:
            with patch('modlingo.api_verification.messagebox.askyesno', return_value=True), patch('modlingo.api_verification.messagebox.showerror'), patch('modlingo.api_verification.verify_connection') as factory:
                factory.side_effect = error
                factory.return_value = [('connection-test', 'Hello', 'Hello')]
                self.app.test_connection()
                self.pump()
            self.assertFalse(self.app.api_ready())
            self.assertTrue(self.app.start_button.instate(['disabled']))
            self.assertIn('验证失败', self.app.api_status.get())
            self.assertNotIn('private-internal-detail', self.app.api_status.get())

    def test_stale_verification_result_cannot_unlock_changed_configuration(self):
        from dataclasses import replace
        from modlingo.provider import preset_config
        self.app.config = preset_config('deepseek', 'contract', 'test-key')
        old = replace(self.app.config)
        old_generation = self.app.verification_generation
        self.app.invalidate_api()
        self.app.finish_api_test((old_generation, old, None))
        self.assertFalse(self.app.api_ready())
        self.assertTrue(self.app.start_button.instate(['disabled']))

    def test_cancelled_verification_sends_no_request(self):
        from modlingo.provider import preset_config
        self.app.config = preset_config('deepseek', 'contract', 'test-key')
        with patch('modlingo.api_verification.messagebox.askyesno', return_value=False), patch('modlingo.api_verification.verify_connection') as factory:
            self.app.test_connection()
            factory.assert_not_called()
        self.assertFalse(self.app.busy)
        self.assertFalse(self.app.api_ready())
