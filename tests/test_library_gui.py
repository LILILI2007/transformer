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
        with patch('modlingo.library_gui.discover_games', return_value=[game]), patch('modlingo.library_gui.scan_mods', return_value=[item]):
            self.app.detect()
            self.pump()
        self.assertEqual(self.app.mods, game / 'Mods')
        self.assertEqual(self.app.tree.set('0', 'pick'), '☑')
        self.app.toggle_index('0')
        self.assertFalse(item.selected)
        self.app.select_all(True)
        self.assertTrue(item.selected)

    def test_multiple_games_require_explicit_choice(self):
        with patch('modlingo.library_gui.discover_games', return_value=[Path('A'), Path('B')]):
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
        self.assertIn('失败或部分完成 1', self.app.summary.get())

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
