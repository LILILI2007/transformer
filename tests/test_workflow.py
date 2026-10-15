"""One end-to-end project cycle with a deliberately simulated model."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modlingo.core import Project, ProjectError
from modlingo.provider import APIConfig, Translator, batches


class WorkflowTests(unittest.TestCase):
    def test_import_translate_edit_reload_update_review_export(self):
        root = Path(__file__).resolve().parent.parent
        project = Project.from_mod(root / "examples" / "DemoMod")
        project.glossary = {"Starfruit": "杨桃"}
        translator = Translator(APIConfig("https://example.test", "simulated", "not-a-real-key"))
        calls = []

        def fake_model(messages):
            inputs = json.loads(messages[1]["content"])["items"]
            calls.extend(value["context_key"] for value in inputs.values())
            # Only tests mechanics, explicitly NOT semantic translation quality.
            return json.dumps({key: "测试译文：" + value["text"].replace("Starfruit", "杨桃") for key, value in inputs.items()}, ensure_ascii=False)

        def run_pending():
            pending = [(key, entry.source) for key, entry in project.entries.items() if entry.needs_translation()]
            with patch.object(translator, "_request", side_effect=fake_model):
                for batch in batches(pending):
                    for key, source, target in translator.translate(batch, project.glossary):
                        self.assertEqual(project.accept_ai(key, source, target), [])

        run_pending()
        self.assertEqual(len(calls), 6)
        project.edit("item.starfruit.name", "玩家确定的杨桃果酱")
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "project.json"
            output = Path(folder) / "translation.zip"
            project.save(state)
            project = Project.load(state)
            first = project.export_zip(output)
            self.assertEqual(first["exported"], 7)
            self.assertEqual(len(first["skipped"]), 2)
            calls.clear()
            project.merge_update(Project.from_mod(root / "examples" / "DemoMod-v2"))
            run_pending()
            self.assertEqual(set(calls), {"dialogue.greeting", "ui.new"})
            self.assertEqual(project.entries["item.starfruit.name"].target, "玩家确定的杨桃果酱")
            self.assertFalse(any(entry.needs_translation() for entry in project.entries.values()))
            with self.assertRaises(ProjectError):
                project.export_zip(output)
            project.edit("dialogue.greeting", project.entries["dialogue.greeting"].target)
            second = project.export_zip(output)
            self.assertEqual(second["exported"], 8)
            self.assertEqual(second["retired_count"], 2)
