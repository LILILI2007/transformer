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


def parse_jsonc(text: str, *, numeric_properties=False):
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
    if numeric_properties:
        # Newtonsoft accepts bare numeric field IDs (e.g. CP Fields: {4: ...}).
        # Normalize only tokens outside strings, never values or player files.
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
            elif char in "{,":
                start = i + 1
                while start < len(chars) and chars[start].isspace():
                    start += 1
                end = start
                while end < len(chars) and chars[end] in "0123456789":
                    end += 1
                colon = end
                while colon < len(chars) and chars[colon].isspace():
                    colon += 1
                if end > start and colon < len(chars) and chars[colon] == ":":
                    chars[end:end] = ['"']
                    chars[start:start] = ['"']
                    i = end + 1
            i += 1
    try:
        return json.loads("".join(chars), object_pairs_hook=_unique_object)
    except (ValueError, TypeError) as exc:
        raise ProjectError("JSON 格式错误：" + str(exc)) from exc


def read_jsonc(path: Path, *, numeric_properties=False):
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ProjectError("单个文件超过 20 MB，首版暂不处理")
    try:
        return parse_jsonc(path.read_text(encoding="utf-8-sig"), numeric_properties=numeric_properties)
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


def locale_paths(folder, locale):
    """SMAPI permits either top-level locale files or locale directories."""
    i18n = Path(folder) / 'i18n'
    for path in (i18n, i18n / locale):
        if path.exists() and (path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ProjectError('语言目录使用链接或目录联接，已拒绝')
    if not i18n.exists():
        return []
    if any(i18n.glob('*.json')) and any(p.is_dir() and any(p.glob('*.json')) for p in i18n.iterdir()):
        raise ProjectError('语言目录混用了平铺文件和分文件目录，SMAPI 不支持此布局')
    directory = i18n / locale
    if directory.is_dir():
        if any(p.is_dir() for p in directory.iterdir()):
            raise ProjectError('语言目录含多层子目录，暂不处理')
        paths = sorted(directory.glob('*.json'))
    else:
        path = i18n / (locale + '.json')
        paths = [path] if path.is_file() else []
    if len(paths) > 2048 or sum(p.stat().st_size for p in paths) > 40 * 1024 * 1024:
        raise ProjectError('语言目录超过文件数量或总大小限制')
    for path in paths:
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ProjectError('语言文件使用链接或目录联接，已拒绝')
    return paths


def merge_language_files(paths):
    result, seen = {}, set()
    for path in paths:
        for key, value in language_map(path).items():
            if key.casefold() in seen:
                raise ProjectError('分文件语言目录存在重复键：' + key)
            seen.add(key.casefold())
            result[key] = value
    return result


def translation_target(folder):
    i18n = Path(folder) / 'i18n'
    return i18n / 'zh' / 'ModLingo.generated.json' if (i18n / 'default').is_dir() else i18n / 'zh.json'


# Protect common formatting, while leaving commands and nested grammar blocked.
TOKEN = re.compile(r"\{\{[^{}\r\n]+\}\}|\{\d+(?:,[+-]?\d+)?(?::[^{}\r\n]+)?\}|#\$[be]#|\$\d+(?!\d)|\$[hsluna](?![A-Za-z0-9_])|%[A-Za-z_][A-Za-z0-9_]*|\[\d+(?:\s+\d+)*\s*\]|[@/^#%|~]|\r?\n")
MASK_TOKEN = re.compile(r'\$query\b[^#\r\n]*#|\$action\b(?:"(?:\\.|[^"\\\r\n])*"|[^"#|\r\n])*|\$(?:q|r|p|c|d|t)\b[^#\r\n]*#|'+TOKEN.pattern)


def _unsupported_reason(source: str) -> str:
    if re.search(r'(?:^|[/\\])\s*(?:quickQuestion\b|textAboveHead\b|end\s+dialogue(?:WarpOut)?(?:\s|$))',source):
        return '含快速问答、头顶气泡或结束对白，需结构化翻译'
    residual = TOKEN.sub("", source)
    if '/' in source and re.search(r'(?:^|/)\s*(?:(?:speak|message|pause|warp|faceDirection|move|animate|skippable|end|playMusic|stopMusic|viewport|positionOffset|addItem|friendship|emote|globalFade|fade|question|quickQuestion)\b|farmer\s+-?\d)', source):
        return '含事件命令序列，暂不自动改写脚本'
    if re.search(r'#(?:attachment|action|reply|conversation|item|money|recipe|quest|autoOpen|title|image)\b', source):
        return '含邮件命令或附件语法，暂不自动翻译'
    if any(char in residual for char in ("{", "}", "$", "[", "]")):
        return "含复杂或未知控制语法，首版不自动翻译或导出此项"
    if len(source) > 16000:
        return "单条文本超过 16000 字符，首版暂不处理"
    return ""


def unsupported_reason(source: str) -> str:
    return _unsupported_reason(source)


def validate_translation(source: str, target: str) -> List[str]:
    issues = []
    if _unsupported_reason(source):
        from .syntax import spans, validate_rebuilt, kind_for
        kind = kind_for(source)
        parts, _ = spans(source, kind)
        if parts and target.strip() and validate_rebuilt(source, target, kind):
            return []
        return ['复杂文本的非文字结构发生变化或尚无可处理片段']
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
        if self.origin != 'manual' and (self.error or (self.target and validate_translation(self.source, self.target))):
            return True
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
    language_layout: str = 'flat'
    external_chinese_keys: List[str] = field(default_factory=list)
    structured_support: bool = False

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
        source_paths = locale_paths(folder, 'default')
        if not source_paths:
            if (i18n / 'default').is_dir():
                raise ProjectError('分文件语言目录没有可读取的 JSON 文件')
            raise ProjectError("没有 i18n/default.json；首版不提取 content.json、DLL 或 XNB 的硬编码文本")
        source = merge_language_files(source_paths)
        chinese_paths = locale_paths(folder, 'zh')
        chinese = merge_language_files(chinese_paths)
        folded = {key.casefold(): value for key, value in chinese.items()}
        project = cls(manifest["UniqueID"], str(manifest.get("Name", folder.name)), safe_folder(folder.name), str(manifest.get("Version", "")))
        project.language_layout = 'split' if (i18n / 'default').is_dir() else 'flat'
        if project.language_layout == 'split':
            project.external_chinese_keys = list(merge_language_files([p for p in chinese_paths if p.name.casefold() != 'modlingo.generated.json']))
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
        self.language_layout, self.external_chinese_keys = incoming.language_layout, incoming.external_chinese_keys
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
            if type(project.structured_support) is not bool:
                raise ProjectError('结构化文本开关无效')
            if project.language_layout not in ('flat', 'split') or not isinstance(project.external_chinese_keys, list) or not all(isinstance(k, str) for k in project.external_chinese_keys):
                raise ProjectError('项目语言布局无效')
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
        from .syntax import spans, kind_for
        path = Path(path)
        if path.suffix.casefold() != ".zip":
            raise ProjectError("翻译包必须保存为 .zip，不能覆盖 JSON 语言文件")
        translations, skipped, blockers = {}, [], []
        external = {k.casefold() for k in self.external_chinese_keys}
        for key, entry in self.entries.items():
            if key.casefold() in external:
                if entry.origin != 'imported':
                    blockers.append(key + '：分文件目录的已有中文需在原文件中编辑，避免重复键')
                continue
            if unsupported_reason(entry.source) and not (self.structured_support and spans(entry.source,kind_for(entry.source))[0]):
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
        relative = 'i18n/zh/ModLingo.generated.json' if self.language_layout == 'split' else 'i18n/zh.json'
        instructions = f'ModLingo 翻译包\n目标：{self.name} / {self.mod_id}\n退出游戏，备份原文件后，将 {folder}/{relative} 复制到原 mod 的对应路径。不要当作独立 mod 安装。\n分文件目录保留其他中文文件，只补充缺少的键。恢复时复制回备份，原先没有此文件则删除新文件。\n导出 {len(translations)} 项，跳过 {len(skipped)} 项。结构检查不等于实机验收。\n'
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=str(path.parent), suffix=".zip.tmp")
        os.close(handle)
        try:
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(folder + '/' + relative, json.dumps(translations, ensure_ascii=False, indent=2))
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
