"""Bounded game discovery and a pinned, read-only inventory of installed mods."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .core import Project, ProjectError, read_jsonc


def linked(path):
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    except OSError:
        return False


def within(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def game_root(path):
    path = Path(path).expanduser()
    if path.name.casefold() == 'mods':
        path = path.parent
    if not any((path / name).is_file() for name in ('Stardew Valley.exe', 'StardewValley.exe', 'StardewValley', 'Stardew Valley.dll')):
        raise ProjectError('这里不是可识别的星露谷游戏目录，请选择包含游戏程序的文件夹')
    if not (path / 'Mods').is_dir():
        raise ProjectError('已找到游戏，但没有 Mods 文件夹；请先安装 SMAPI 和 mod')
    if linked(path / 'Mods'):
        raise ProjectError('Mods 是链接或目录联接，暂不自动写入；请选择实际游戏目录')
    return path.resolve()


def registry_paths():
    if os.name != 'nt':
        return []
    import winreg
    paths = []
    keys = [(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 413150', 'InstallLocation'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\GOG.com\Games\1453375253', 'path')]
    for hive, key, name in keys:
        try:
            with winreg.OpenKey(hive, key) as handle:
                value, _ = winreg.QueryValueEx(handle, name)
                if isinstance(value, str) and value:
                    paths.append(Path(value))
        except OSError:
            pass
    return paths


def steam_games(steam):
    libraries = [Path(steam)]
    vdf = Path(steam) / 'steamapps' / 'libraryfolders.vdf'
    try:
        if vdf.stat().st_size <= 2 * 1024 * 1024:
            text = vdf.read_text(encoding='utf-8-sig')
            for value in re.findall(r'"path"\s*"((?:\\.|[^"\\])*)"', text, re.I):
                libraries.append(Path(value.replace('\\\\', '\\').replace('\\"', '"')))
    except (OSError, UnicodeError):
        pass
    for library in libraries:
        apps = library / 'steamapps'
        yield apps / 'common' / 'Stardew Valley'
        try:
            manifest = apps / 'appmanifest_413150.acf'
            if manifest.stat().st_size <= 1024 * 1024:
                match = re.search(r'"installdir"\s*"([^"\r\n]+)"', manifest.read_text(encoding='utf-8-sig'))
                if match and '/' not in match[1] and '\\' not in match[1] and match[1] not in ('.', '..'):
                    yield apps / 'common' / match[1]
        except (OSError, UnicodeError):
            pass


def discover_games(saved=None, roots=None):
    """Inspect registry, Steam libraries and known folders; never crawl disks."""
    seeds = list(roots) if roots is not None else registry_paths()
    if roots is None:
        for name in ('ProgramFiles(x86)', 'ProgramFiles'):
            if os.environ.get(name):
                base = Path(os.environ[name])
                seeds.extend([base / 'Steam', base / 'GOG Galaxy' / 'Games' / 'Stardew Valley'])
        for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
            base = Path(letter + ':/')
            seeds.extend([base / 'SteamLibrary', base / 'Steam', base / 'Games' / 'Steam',
                          base / 'GOG Games' / 'Stardew Valley', base / 'XboxGames' / 'Stardew Valley' / 'Content'])
    candidates = [Path(saved)] if saved else []
    for seed in seeds:
        candidates.append(Path(seed))
        candidates.extend(steam_games(seed))
    found = {}
    for candidate in candidates:
        try:
            root = game_root(candidate)
            found[str(root).casefold()] = root
        except (ProjectError, OSError):
            pass
    return list(found.values())


def fingerprint(folder):
    digest = hashlib.sha256()
    for name in ('manifest.json', 'i18n/default.json', 'i18n/zh.json'):
        path = Path(folder) / name
        if linked(path) or linked(path.parent):
            raise ProjectError('mod 文本使用链接或目录联接，暂不自动写入')
        digest.update(name.encode())
        if path.exists():
            if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
                raise ProjectError('语言文件不是普通文件或超过 20 MB')
            digest.update(b'present' + path.read_bytes())
        else:
            digest.update(b'absent')
    return digest.hexdigest()


@dataclass
class ModItem:
    folder: Path
    name: str
    mod_id: str = ''
    version: str = ''
    reason: str = ''
    snapshot: str = ''
    selected: bool = True


def scan_mods(mods):
    mods = Path(mods).resolve()
    results, pending = [], [(mods, 0)]
    visited = 0
    while pending:
        folder, depth = pending.pop()
        visited += 1
        if visited > 20000:
            raise ProjectError('目录数量超过 20000，请选择更小的 Mods 目录')
        if folder != mods and (folder / 'manifest.json').is_file():
            item = ModItem(folder, folder.name)
            try:
                manifest = read_jsonc(folder / 'manifest.json')
                if isinstance(manifest, dict):
                    item.name = str(manifest.get('Name', folder.name))
                    item.mod_id = str(manifest.get('UniqueID', ''))
                    item.version = str(manifest.get('Version', ''))
                item.snapshot = fingerprint(folder)
                Project.from_mod(folder)
            except (ProjectError, OSError) as exc:
                item.reason = str(exc)
            results.append(item)
        if depth >= 12:
            continue
        try:
            for child in sorted(folder.iterdir(), reverse=True):
                if child.is_dir() and not child.name.startswith('.'):
                    if linked(child):
                        results.append(ModItem(child, child.name, reason='链接或目录联接已跳过，不会跟随到其他目录'))
                    else:
                        pending.append((child, depth + 1))
        except OSError:
            results.append(ModItem(folder, folder.name, reason='无法读取目录，请检查权限'))
    identities = {}
    for item in results:
        if item.mod_id:
            identities.setdefault(item.mod_id.casefold(), []).append(item)
    for group in identities.values():
        if len(group) > 1:
            for item in group:
                item.reason = '存在重复 UniqueID，请先移除重复安装，避免翻译到错误副本'
    return sorted(results, key=lambda item: (item.name.casefold(), str(item.folder)))
