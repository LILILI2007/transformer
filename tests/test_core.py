import json
import tempfile
import unittest
import zipfile
from dataclasses import asdict
from pathlib import Path

from modlingo.core import Entry, Project, ProjectError, language_map, parse_jsonc, safe_folder, unsupported_reason, validate_translation

ROOT = Path(__file__).resolve().parent.parent


class JSONCTests(unittest.TestCase):
    def test_comments_and_trailing_commas_without_damaging_strings(self):
        data = parse_jsonc('\ufeff{ /* 文档 */ "url":"https://farm.test/a//b", "quote":"a\\\"//b", //注释\n"text":"/*literal*/ ,}", }')
        self.assertEqual(data["url"], "https://farm.test/a//b")
        self.assertEqual(data["quote"], 'a"//b')
        self.assertEqual(data["text"], "/*literal*/ ,}")

    def test_unclosed_comment_and_case_collision_are_rejected(self):
        for data in ['{"a":"x" /* no end', '{"Name":"a","name":"b"}', '{"a":"1","a":"2"}']:
            with self.assertRaises(ProjectError):
                parse_jsonc(data)

    def test_only_flat_strings_and_schema_is_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "language.json"
            for value in [4, True, {}, [], None]:
                path.write_text(json.dumps({"key": value}), encoding="utf-8")
                with self.assertRaises(ProjectError):
                    language_map(path)
            path.write_text('{"$schema":"https://example.test/schema","a":"Hello"}', encoding="utf-8")
            self.assertEqual(language_map(path), {"a": "Hello"})


class ValidationTests(unittest.TestCase):
    def test_common_tokens_and_dialogue_survive(self):
        source = "Hello {{Player.Name}}!#$b#Your %farm farm, @.$h\nBye."
        target = "你好 {{Player.Name}}！#$b#你的 %farm 农场，@。$h\n再见。"
        self.assertEqual(validate_translation(source, target), [])

    def test_missing_extra_and_reordered_tokens_are_blocked(self):
        source = "{{a}} and {{b}}$h"
        for target in ["{{a}}你好$h", "{{a}} {{b}} {{b}}$h", "{{b}} {{a}}$h", "{{a}} {{b}}$q"]:
            self.assertTrue(validate_translation(source, target))

    def test_complex_grammar_is_not_claimed_supported(self):
        for source in ["$q 1/2 Question?", "${male^female}$", "Hello [123]", "message#attachment", "{{outer: {{inner}}}}"]:
            self.assertTrue(unsupported_reason(source))

    def test_newlines_are_checked(self):
        self.assertTrue(validate_translation("First\nSecond", "第一第二"))


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.project = Project.from_mod(ROOT / "examples" / "DemoMod")

    def test_import_preserves_chinese_and_source_files(self):
        paths = list((ROOT / "examples" / "DemoMod").rglob("*.json"))
        before = {str(path): path.read_bytes() for path in paths}
        self.assertEqual(self.project.entries["ui.open"].target, "打开温室")
        self.assertFalse(self.project.entries["ui.open"].needs_translation())
        with tempfile.TemporaryDirectory() as folder:
            self.project.edit("item.starfruit.name", "杨桃果酱")
            self.project.export_zip(Path(folder) / "zh.zip")
        self.assertEqual(before, {str(path): path.read_bytes() for path in paths})

    def test_update_preserves_manual_and_marks_source_changes(self):
        self.project.edit("dialogue.greeting", "你好，{{PlayerName}}！欢迎来到农场。$h")
        self.project.edit("item.starfruit.name", "玩家自选杨桃果酱")
        self.project.merge_update(Project.from_mod(ROOT / "examples" / "DemoMod-v2"))
        self.assertEqual(self.project.entries["item.starfruit.name"].target, "玩家自选杨桃果酱")
        self.assertFalse(self.project.entries["item.starfruit.name"].needs_translation())
        changed = self.project.entries["dialogue.greeting"]
        self.assertEqual(changed.status(), "更新待确认")
        self.assertIn("欢迎来到", changed.previous_target)
        self.assertTrue(changed.needs_translation())
        self.assertIn("ui.new", self.project.entries)
        self.assertIn("event.complex", self.project.retired)
        self.assertNotIn("event.complex", self.project.entries)

    def test_changed_candidate_is_not_repeated_and_requires_confirmation(self):
        self.project.edit("dialogue.greeting", "你好，{{PlayerName}}！欢迎来到农场。$h")
        self.project.merge_update(Project.from_mod(ROOT / "examples" / "DemoMod-v2"))
        key = "dialogue.greeting"
        self.project.accept_ai(key, self.project.entries[key].source, "你好，{{PlayerName}}！欢迎回到农场。$h")
        self.assertFalse(self.project.entries[key].needs_translation())
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ProjectError):
                self.project.export_zip(Path(folder) / "zh.zip")
            self.project.edit(key, self.project.entries[key].target)
            self.project.export_zip(Path(folder) / "zh.zip")

    def test_failure_and_manual_bad_tokens_block_export(self):
        self.project.edit("dialogue.greeting", "你好！")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out.zip"
            path.write_bytes(b"previous export")
            with self.assertRaises(ProjectError):
                self.project.export_zip(path)
            self.assertEqual(path.read_bytes(), b"previous export")

    def test_empty_translation_is_omitted_not_empty_string(self):
        self.project.edit("item.starfruit.name", "")
        self.assertFalse(self.project.entries["item.starfruit.name"].needs_translation())
        self.assertEqual(self.project.entries["item.starfruit.name"].status(), "原文回退")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out.zip"
            report = self.project.export_zip(path)
            with zipfile.ZipFile(path) as archive:
                data = json.loads(archive.read("DemoMod/i18n/zh.json"))
                self.assertNotIn("item.starfruit.name", data)
                self.assertEqual(data, {"ui.open": "打开温室"})
                self.assertFalse(report["verified_in_game"])
                self.assertFalse(any("manifest.json" in name for name in archive.namelist()))

    def test_empty_source_update_requires_translation_and_confirmation(self):
        project = Project("A.B", "Demo", "Demo", entries={"key": Entry("", "旧译文", "imported", "")})
        project.merge_update(Project("A.B", "Demo", "Demo", entries={"key": Entry("New text")}))
        self.assertTrue(project.entries["key"].needs_translation())
        project.accept_ai("key", "New text", "新文本")
        self.assertTrue(project.entries["key"].needs_review)
        self.assertFalse(project.entries["key"].needs_translation())
        project.edit("key", "新文本")
        self.assertFalse(project.entries["key"].needs_review)

    def test_manual_fallback_is_preserved_until_source_changes(self):
        key = "item.starfruit.name"
        self.project.edit(key, "")
        self.project.merge_update(Project.from_mod(ROOT / "examples" / "DemoMod-v2"))
        self.assertFalse(self.project.entries[key].needs_translation())
        incoming = Project.from_mod(ROOT / "examples" / "DemoMod-v2")
        incoming.entries[key].source = "New jam"
        self.project.merge_update(incoming)
        self.assertTrue(self.project.entries[key].needs_translation())

    def test_project_round_trip_retains_history_and_glossary(self):
        self.project.glossary = {"Starfruit": "杨桃"}
        self.project.edit("item.starfruit.name", "杨桃果酱")
        self.project.merge_update(Project.from_mod(ROOT / "examples" / "DemoMod-v2"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "project.json"
            self.project.save(path)
            loaded = Project.load(path)
            self.assertEqual(asdict(loaded), asdict(self.project))
            self.assertNotIn("api_key", path.read_text(encoding="utf-8"))

    def test_wrong_mod_identity_is_rejected(self):
        other = Project("Someone.OtherMod", "Other", "Other")
        with self.assertRaises(ProjectError):
            self.project.merge_update(other)

    def test_export_cannot_overwrite_json_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "zh.json"
            path.write_text('{"original":"keep"}', encoding="utf-8")
            with self.assertRaises(ProjectError):
                self.project.export_zip(path)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"original":"keep"}')

    def test_suspicious_output_and_glossary_need_review(self):
        self.project.glossary = {"Starfruit": "杨桃"}
        key = "item.starfruit.name"
        source = self.project.entries[key].source
        self.project.accept_ai(key, source, "星星水果果酱")
        self.assertEqual(self.project.entries[key].status(), "译文待确认")
        self.assertFalse(self.project.entries[key].needs_translation())

    def test_stale_response_does_not_overwrite_new_source(self):
        with self.assertRaises(ProjectError):
            self.project.accept_ai("ui.open", "old source", "错误候选")
        self.assertEqual(self.project.entries["ui.open"].target, "打开温室")

    def test_archive_names_are_sanitized(self):
        self.assertNotIn("/", safe_folder("../../escape"))
        self.assertEqual(safe_folder("CON"), "TranslatedMod")
        self.project.folder = "../../escape"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out.zip"
            self.project.export_zip(path)
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    self.assertNotIn("..", name.split("/"))

    def test_unsupported_directory_layout_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "manifest.json").write_text('{"UniqueID":"A.B"}', encoding="utf-8")
            (root / "i18n" / "default").mkdir(parents=True)
            with self.assertRaisesRegex(ProjectError, "分文件"):
                Project.from_mod(root)


if __name__ == "__main__":
    unittest.main()
