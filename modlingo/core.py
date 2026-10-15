"""Pure project, JSONC, validation and export logic. Never writes to a mod."""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List


class ProjectError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    seen = set()
    for key, value in pairs:
        folded = key.casefold()
        if folded in seen:
            raise ProjectError("存在重复或仅大小写不同的键：" + key)
        seen.add(folded)
        result[key] = value
    return result


def parse_jsonc(text: str):
    """Strip comments/trailing commas with a lexer, preserving quoted content."""
    chars = list(text.lstrip("\ufeff"))
    i, quoted = 0, False
    while i < len(chars):
        char = chars[i]
        if quoted:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "/" and i + 1 < len(chars) and chars[i + 1] == "/":
            while i < len(chars) and chars[i] not in "\r\n":
                chars[i] = " "
                i += 1
            continue
        elif char == "/" and i + 1 < len(chars) and chars[i + 1] == "*":
            chars[i] = chars[i + 1] = " "
            i += 2
            while i + 1 < len(chars) and not (chars[i] == "*" and chars[i + 1] == "/"):
                if chars[i] not in "\r\n":
                    chars[i] = " "
                i += 1
            if i + 1 >= len(chars):
                raise ProjectError("JSON 注释没有闭合")
            chars[i] = chars[i + 1] = " "
            i += 2
            continue
        i += 1
    i, quoted = 0, False
    while i < len(chars):
        char = chars[i]
        if quoted:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == ",":
            j = i + 1
            while j < len(chars) and chars[j].isspace():
                j += 1
            if j < len(chars) and chars[j] in "}]":
                chars[i] = " "
        i += 1
    try:
        return json.loads("".join(chars), object_pairs_hook=_unique_object)
    except (ValueError, TypeError) as exc:
        raise ProjectError("JSON 格式错误：" + str(exc)) from exc


def read_jsonc(path: Path):
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ProjectError("单个文件超过 20 MB，首版暂不处理")
    try:
        return parse_jsonc(path.read_text(encoding="utf-8-sig"))
    except UnicodeError as exc:
        raise ProjectError("文件不是有效的 UTF-8 编码：" + path.name) from exc


def language_map(path: Path) -> Dict[str, str]:
    data = read_jsonc(path)
    if not isinstance(data, dict):
        raise ProjectError("语言文件必须是平铺键值对象")
    data.pop("$schema", None)
    for key, value in data.items():
        if not isinstance(value, str):
            raise ProjectError("翻译值必须是字符串：" + key)
    return data


# Only a documented conservative subset is accepted. Complex game grammars
# are NOT declared safe merely because token counts match.
TOKEN = re.compile(r"\{\{[^{}\r\n]+\}\}|#\$[be]#|\$[0-5](?!\d)|\$[hsluna](?![A-Za-z0-9_])|%[A-Za-z_][A-Za-z0-9_]*|@")


def unsupported_reason(source: str) -> str:
    residual = TOKEN.sub("", source)
    if any(char in residual for char in ("{", "}", "$", "#", "^", "[", "]", "/", "%")):
        return "含复杂或未知控制语法，首版不自动翻译或导出此项"
    if len(source) > 16000:
        return "单条文本超过 16000 字符，首版暂不处理"
    return ""


def validate_translation(source: str, target: str) -> List[str]:
    issues = []
    if unsupported_reason(source):
        issues.append(unsupported_reason(source))
    if not target.strip():
        issues.append("译文为空")
        return issues
    source_tokens, target_tokens = TOKEN.findall(source), TOKEN.findall(target)
    if Counter(source_tokens) != Counter(target_tokens):
        issues.append("占位符或控制符内容、数量不一致")
    # Dialogue and replacement order are intentionally conservative in v0.1.
    if source_tokens != target_tokens:
        issues.append("占位符或控制符顺序发生变化")
    if unsupported_reason(target):
        issues.append("译文引入复杂或未知控制语法")
    if source.count("\n") != target.count("\n"):
        issues.append("换行数量发生变化")
    return list(dict.fromkeys(issues))


def glossary_warnings(source: str, target: str, glossary: Dict[str, str]) -> List[str]:
    return [f"术语待核对：{term} → {translation}" for term, translation in glossary.items()
            if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", source, re.IGNORECASE)
            and translation not in target]


@dataclass
class Entry:
    source: str
    target: str = ""
    origin: str = ""
    translated_source: str = ""
    previous_source: str = ""
    previous_target: str = ""
    needs_review: bool = False
    error: str = ""

    def status(self) -> str:
        if unsupported_reason(self.source):
            return "暂不支持"
        if self.error or (self.target and validate_translation(self.source, self.target)):
            return "检查失败"
        if self.needs_review:
            return "更新待确认" if self.previous_source else "译文待确认"
        if not self.target.strip():
            return "原文回退" if self.origin == "manual" else "待翻译"
        return "人工已确认" if self.origin == "manual" else "可导出"

    def needs_translation(self) -> bool:
        if unsupported_reason(self.source):
            return False
        if self.needs_review and self.translated_source != self.source:
            return True
        if self.origin == "manual" and not self.target.strip():
            return False
        return not self.target.strip()


def safe_folder(name: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not clean or clean.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *["COM" + str(n) for n in range(1, 10)], *["LPT" + str(n) for n in range(1, 10)]}:
        clean = "TranslatedMod"
    return clean[:100]


@dataclass
class Project:
    mod_id: str
    name: str
    folder: str
    version: str = ""
    entries: Dict[str, Entry] = field(default_factory=dict)
    retired: Dict[str, dict] = field(default_factory=dict)
    glossary: Dict[str, str] = field(default_factory=dict)
    notices: List[str] = field(default_factory=list)

    @classmethod
    def from_mod(cls, folder: Path) -> Project:
        folder = Path(folder).resolve()
        manifest_path = folder / "manifest.json"
        if not manifest_path.is_file():
            raise ProjectError("请选择包含 manifest.json 的单个 mod 文件夹")
        manifest = read_jsonc(manifest_path)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("UniqueID"), str) or not manifest["UniqueID"].strip():
            raise ProjectError("manifest.json 缺少有效的 UniqueID")
        i18n = folder / "i18n"
        if (i18n / "default").is_dir() or (i18n / "zh").is_dir():
            raise ProjectError("检测到分文件语言目录；首版仅支持 default.json / zh.json 平铺布局")
        if not (i18n / "default.json").is_file():
            raise ProjectError("没有 i18n/default.json；首版不提取 content.json、DLL 或 XNB 的硬编码文本")
        source = language_map(i18n / "default.json")
        chinese = language_map(i18n / "zh.json") if (i18n / "zh.json").is_file() else {}
        folded = {key.casefold(): value for key, value in chinese.items()}
        project = cls(manifest["UniqueID"], str(manifest.get("Name", folder.name)), safe_folder(folder.name), str(manifest.get("Version", "")))
        for key, value in source.items():
            target = folded.get(key.casefold(), "")
            project.entries[key] = Entry(value, target, "imported" if target else "", value if target else "")
        source_keys = {key.casefold() for key in source}
        project.retired = {key: {"target": value, "reason": "首次导入时原文中没有此键"}
                           for key, value in chinese.items() if key.casefold() not in source_keys}
        if chinese:
            project.notices.append("已保留现有中文。首次导入没有历史原文，无法判断这些译文是否过期。")
        if project.retired:
            project.notices.append("现有中文中的额外键已归档，不进入新翻译包。")
        project.notices.append("默认语言按英语处理；请确认 default.json 的实际语言。复杂语法项会跳过并回退原文。")
        return project

    def merge_update(self, incoming: Project):
        if self.mod_id.casefold() != incoming.mod_id.casefold():
            raise ProjectError("UniqueID 不同，不能当作同一个 mod 更新；请新建项目")
        old = {key.casefold(): (key, value) for key, value in self.entries.items()}
        result = {}
        for key, fresh in incoming.entries.items():
            pair = old.pop(key.casefold(), None)
            if pair is None:
                # An update's bundled zh is untracked: new source starts pending.
                result[key] = Entry(fresh.source)
                continue
            _, previous = pair
            if previous.source != fresh.source:
                previous.previous_source = previous.source
                previous.previous_target = previous.target
                previous.source = fresh.source
                previous.needs_review = True
                previous.error = ""
            result[key] = previous
        for key, previous in old.values():
            self.retired[key] = {**asdict(previous), "reason": "更新后原文删除此键"}
        self.entries = result
        self.version, self.folder, self.name = incoming.version, incoming.folder, incoming.name
        self.notices = ["更新已比对：未变项保留，变更项需确认，新增项待翻译，删除项已归档。"]

    def edit(self, key: str, target: str):
        entry = self.entries[key]
        entry.target = target
        entry.origin = "manual"
        entry.error = ""
        entry.translated_source = entry.source
        if not target.strip():
            entry.target = ""
            entry.needs_review = False
            return []
        issues = validate_translation(entry.source, target)
        entry.needs_review = bool(issues)
        return issues

    def accept_ai(self, key: str, source: str, target: str):
        entry = self.entries[key]
        if entry.source != source:
            raise ProjectError("原文已变化，此响应没有写入，请重新翻译")
        issues = validate_translation(source, target)
        if issues:
            entry.error = "；".join(issues)
            return issues
        changed = entry.needs_review
        entry.target, entry.origin, entry.translated_source = target, "ai", source
        entry.error = ""
        entry.needs_review = changed or target.strip() == source.strip() or bool(glossary_warnings(source, target, self.glossary))
        return []

    def save(self, path: Path):
        atomic_write(Path(path), json.dumps({"schema_version": 1, "project": asdict(self)}, ensure_ascii=False, indent=2).encode("utf-8"))

    @classmethod
    def load(cls, path: Path) -> Project:
        try:
            data = read_jsonc(Path(path))
            if data.get("schema_version") != 1:
                raise ProjectError("不支持的项目版本")
            obj = data["project"]
            entries = {key: Entry(**entry) for key, entry in obj.pop("entries").items()}
            project = cls(entries=entries, **obj)
            string_fields = ("source", "target", "origin", "translated_source", "previous_source", "previous_target", "error")
            if not all(isinstance(key, str) and all(isinstance(getattr(e, attr), str) for attr in string_fields)
                       and isinstance(e.needs_review, bool) for key, e in entries.items()):
                raise ProjectError("项目条目格式无效")
            if not all(isinstance(getattr(project, attr), str) for attr in ("mod_id", "name", "folder", "version")):
                raise ProjectError("项目身份信息无效")
            if not isinstance(project.glossary, dict) or not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in project.glossary.items()):
                raise ProjectError("项目术语表格式无效")
            if not isinstance(project.retired, dict) or not isinstance(project.notices, list) or not all(isinstance(v, str) for v in project.notices):
                raise ProjectError("项目历史或提示格式无效")
            if len({key.casefold() for key in entries}) != len(entries):
                raise ProjectError("项目键冲突")
            project.folder = safe_folder(project.folder)
            return project
        except (KeyError, TypeError, AttributeError) as exc:
            raise ProjectError("项目文件结构无效") from exc

    def export_zip(self, path: Path):
        path = Path(path)
        if path.suffix.casefold() != ".zip":
            raise ProjectError("翻译包必须保存为 .zip，不能覆盖 JSON 语言文件")
        translations, skipped, blockers = {}, [], []
        for key, entry in self.entries.items():
            if unsupported_reason(entry.source):
                skipped.append({"key": key, "reason": unsupported_reason(entry.source)})
                continue
            if entry.needs_review:
                blockers.append(key + "：请先确认译文或清空以使用原文")
                continue
            if entry.error:
                blockers.append(key + "：" + entry.error)
                continue
            if not entry.target.strip():
                skipped.append({"key": key, "reason": "未翻译，使用默认语言回退"})
                continue
            issues = validate_translation(entry.source, entry.target)
            if issues:
                blockers.append(key + "：" + "；".join(issues))
            else:
                translations[key] = entry.target
        if blockers:
            raise ProjectError("导出被阻止（共 %d 项）：\n%s" % (len(blockers), "\n".join(blockers[:8])))
        if not translations:
            raise ProjectError("没有可导出的译文。请先翻译或编辑至少一条支持的文本。")
        folder = safe_folder(self.folder)
        report = {"mod_id": self.mod_id, "mod_version": self.version, "language": "zh", "exported": len(translations), "skipped": skipped,
                  "retired_count": len(self.retired), "verified_in_game": False}
        instructions = f"ModLingo 翻译包\n\n目标 mod：{self.name}\nUniqueID：{self.mod_id}\n版本：{self.version}\n\n1. 退出游戏。找到 Mods 中 UniqueID 与上述一致的原 mod 文件夹；文件夹名可以不同。\n2. 如已有 i18n/zh.json，先复制到别处备份。此包会替换整个 zh.json。\n3. 将本包 {folder}/i18n/zh.json 复制到该原 mod 的 i18n 文件夹。不要把翻译包当作独立 mod 安装。\n4. 在游戏中选择中文并启动 SMAPI，检查物品名、界面和相关对话。\n5. 恢复时复制回备份；原先没有中文文件，则删除本次添加的 zh.json。\n\n导出 {len(translations)} 项，跳过 {len(skipped)} 项；跳过项使用默认语言回退。详细情况见 export-report.json。\n首版不支持复杂事件/未知控制语法。结构检查通过不等于实机验证或译文含义正确。\n"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=str(path.parent), suffix=".zip.tmp")
        os.close(handle)
        try:
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(folder + "/i18n/zh.json", json.dumps(translations, ensure_ascii=False, indent=2))
                archive.writestr("安装说明.txt", instructions)
                archive.writestr("export-report.json", json.dumps(report, ensure_ascii=False, indent=2))
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return report


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
