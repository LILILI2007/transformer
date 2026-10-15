import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "GUI tests need a desktop display")
class GUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from modlingo.gui import App
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"LOCALAPPDATA": self.temp.name})
        self.environment.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.app.sample()
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.environment.stop()
        self.temp.cleanup()

    def test_manual_edit_survives_selection_filter_and_project_reload(self):
        from modlingo.core import Project
        key = "item.starfruit.name"
        self.app.tree.selection_set(str(self.app.visible_keys.index(key)))
        self.app.select_entry()
        self.app.target_text.delete("1.0", "end")
        self.app.target_text.insert("1.0", "杨桃果酱")
        self.app.confirm_entry()
        self.app.filter.set("可导出")
        self.app.refresh()
        self.assertIn(key, self.app.visible_keys)
        loaded = Project.load(self.app.project_path)
        self.assertEqual(loaded.entries[key].target, "杨桃果酱")
        self.assertEqual(loaded.entries[key].origin, "manual")

    def test_reimport_restores_progress_and_update_marks_changed(self):
        key = "dialogue.greeting"
        self.app.project.edit(key, "你好，{{PlayerName}}！欢迎来到农场。$h")
        self.app.checkpoint()
        self.app.current_key = None
        self.app.sample()
        self.assertEqual(self.app.project.entries[key].origin, "manual")
        root = Path(__file__).resolve().parent.parent
        with patch("modlingo.gui.filedialog.askdirectory", return_value=str(root / "examples" / "DemoMod-v2")):
            self.app.update_mod()
        self.assertEqual(self.app.project.entries[key].status(), "更新待确认")

    def test_worker_results_are_applied_and_saved_on_ui_thread(self):
        from modlingo.core import Project
        key = "item.starfruit.name"
        source = self.app.project.entries[key].source
        self.app._set_busy(True)
        self.app.events.put(("results", [(key, source, "杨桃果酱")]))
        self.app.events.put(("done", None))
        self.app._poll()
        self.assertFalse(self.app.busy)
        self.assertEqual(Project.load(self.app.project_path).entries[key].target, "杨桃果酱")

    def test_manual_fallback_is_filterable_and_not_pending(self):
        from modlingo.gui import CounterStates
        key = "item.starfruit.name"
        before = CounterStates(self.app.project)["todo"]
        self.app.project.edit(key, "")
        self.app.filter.set("原文回退")
        self.app.refresh()
        self.assertEqual(self.app.visible_keys, [key])
        self.assertEqual(CounterStates(self.app.project)["todo"], before - 1)
        self.assertEqual(CounterStates(self.app.project)["fallback"], 1)

    def test_reopening_current_project_keeps_unsaved_editor_change(self):
        key = "item.starfruit.name"
        self.app.tree.selection_set(str(self.app.visible_keys.index(key)))
        self.app.select_entry()
        self.app.target_text.delete("1.0", "end")
        self.app.target_text.insert("1.0", "保留尚未切换的编辑")
        with patch("modlingo.gui.filedialog.askopenfilename", return_value=str(self.app.project_path)):
            self.app.open_project()
        self.assertEqual(self.app.project.entries[key].target, "保留尚未切换的编辑")
