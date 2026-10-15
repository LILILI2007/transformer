"""Batch translation, guarded installation, durable receipts and rollback."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .core import Project, ProjectError, atomic_write, language_map, read_jsonc, unsupported_reason, validate_translation
from .discovery import fingerprint, linked, within
from .provider import batches


def data_root():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share'))) / 'ModLingo'


def sha(content):
    return hashlib.sha256(content).hexdigest()


@contextmanager
def library_lock(mods, storage):
    directory = Path(storage) / 'locks'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (sha(str(Path(mods).resolve()).casefold().encode()) + '.lock')
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ProjectError('另一个 ModLingo 正在处理这个 Mods 目录，请稍后重试') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def check_game_stopped():
    if os.name != 'nt':
        return
    try:
        result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, timeout=10,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise OSError()
    except (OSError, subprocess.TimeoutExpired):
        raise ProjectError('无法检查游戏是否运行，请稍后重试') from None
    listing = result.stdout.lower()
    if any(name in listing for name in (b'"stardew valley.exe"', b'"stardewvalley.exe"', b'"stardewmoddingapi.exe"')):
        raise ProjectError('请先退出星露谷和 SMAPI，再翻译或恢复')


def validate_item(item, mods):
    folder, mods = Path(os.path.abspath(item.folder)), Path(os.path.abspath(mods))
    if folder.resolve() == mods.resolve() or not within(folder, mods):
        raise ProjectError('mod 不在锁定的 Mods 目录内')
    current = folder
    while True:
        if linked(current):
            raise ProjectError('目录变成链接或目录联接，已停止写入')
        if current == mods:
            break
        if current == current.parent:
            raise ProjectError('目录不在锁定的 Mods 内')
        current = current.parent
    if fingerprint(folder) != item.snapshot:
        raise ProjectError('扫描后 mod 文件发生变化，请重新扫描后再试')


def install(item, project, mods, receipt_dir):
    validate_item(item, mods)
    check_game_stopped()
    target = item.folder / 'i18n' / 'zh.json'
    original = target.read_bytes() if target.exists() else None
    # Preserve bundled extra keys and complex entries. Only validated keys change.
    content = language_map(target) if original is not None else {}
    folded = {key.casefold(): key for key in content}
    installed, skipped = 0, []
    for key, entry in project.entries.items():
        reason = unsupported_reason(entry.source)
        if reason:
            skipped.append({'key': key, 'reason': reason})
            continue
        if entry.error or entry.needs_review:
            raise ProjectError('仍有待复核或错误条目，未安装：' + key)
        if not entry.target.strip():
            if entry.origin == 'manual':
                content.pop(folded.get(key.casefold(), key), None)
            skipped.append({'key': key, 'reason': '主动选择原文回退或尚无译文'})
            continue
        issues = validate_translation(entry.source, entry.target)
        if issues:
            raise ProjectError('译文结构检查失败：' + key)
        content.pop(folded.get(key.casefold(), key), None)
        content[key] = entry.target
        installed += 1
    if not installed:
        reasons = '；'.join(dict.fromkeys(item['reason'] for item in skipped))
        raise ProjectError('没有可安装的译文，原文件未修改' + ('：' + reasons if reasons else ''))
    encoded = json.dumps(content, ensure_ascii=False, indent=2).encode('utf-8')
    if original == encoded:
        return {'installed': installed, 'skipped': skipped, 'receipt': '', 'changed': False}
    receipt_dir = Path(receipt_dir)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    backup = receipt_dir / 'original-zh.bin'
    if original is not None:
        atomic_write(backup, original)
    receipt = {'schema_version': 1, 'mod_id': project.mod_id, 'target': str(target), 'mods': str(mods),
               'had_original': original is not None, 'original_sha': sha(original) if original is not None else '',
               'installed_sha': sha(encoded), 'restored': False}
    receipt_path = receipt_dir / 'receipt.json'
    atomic_write(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2).encode('utf-8'))
    # Recheck after writing the backup, immediately before committing the file.
    validate_item(item, mods)
    atomic_write(target, encoded)
    return {'installed': installed, 'skipped': skipped, 'receipt': str(receipt_path), 'changed': True}


def restore(receipt_path):
    receipt_path = Path(receipt_path)
    data = read_jsonc(receipt_path)
    if data.get('schema_version') != 1 or data.get('restored'):
        raise ProjectError('恢复记录无效或已经恢复')
    target, mods = Path(os.path.abspath(data['target'])), Path(os.path.abspath(data['mods']))
    if target.name != 'zh.json' or target.parent.name != 'i18n' or not within(target, mods):
        raise ProjectError('恢复目标不在原 Mods 目录内')
    current = target
    while True:
        if linked(current):
            raise ProjectError('恢复路径包含链接或目录联接，已拒绝')
        if current == mods:
            break
        if current == current.parent:
            raise ProjectError('恢复目录不在原 Mods 内')
        current = current.parent
    check_game_stopped()
    if not target.is_file() or sha(target.read_bytes()) != data['installed_sha']:
        raise ProjectError('安装后中文文件被修改或删除，未覆盖你的后续修改')
    manifest = read_jsonc(target.parent.parent / 'manifest.json')
    if manifest.get('UniqueID', '').casefold() != data['mod_id'].casefold():
        raise ProjectError('mod 身份已变化，已停止恢复')
    if data['had_original']:
        content = (receipt_path.parent / 'original-zh.bin').read_bytes()
        if sha(content) != data['original_sha']:
            raise ProjectError('备份校验失败，未修改原文件')
        atomic_write(target, content)
    else:
        target.unlink()
    data['restored'] = True
    atomic_write(receipt_path, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))


def run_batch(items, mods, translator, stop_event, storage=None, notify=lambda result: None):
    """One mod fails independently; every selection gets a durable result."""
    storage, mods = Path(storage or data_root()), Path(mods).resolve()
    run_id = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    report_path = storage / 'reports' / (run_id + '.json')
    report = {'schema_version': 1, 'run_id': run_id, 'mods': str(mods), 'target_language': 'zh',
              'success': [], 'failure': [], 'verified_in_game': False}
    fatal = ''
    with library_lock(mods, storage):
        check_game_stopped()
        for index, item in enumerate(items):
            result = {'name': item.name, 'mod_id': item.mod_id, 'folder': str(item.folder),
                      'installed': 0, 'skipped': [], 'receipt': '', 'status': '失败', 'reason': ''}
            try:
                if stop_event.is_set():
                    raise ProjectError('用户停止，未安装此 mod')
                if fatal:
                    raise ProjectError('服务配置错误，剩余 mod 未请求：' + fatal)
                if item.reason:
                    raise ProjectError(item.reason)
                validate_item(item, mods)
                project = Project.from_mod(item.folder)
                # Include physical install path: separate game copies must not share progress.
                identity = sha((str(item.folder.resolve()).casefold() + '\0' + project.mod_id.casefold()).encode())[:24]
                state = storage / 'batch-projects' / (identity + '.json')
                disk_state = state.with_suffix('.disk.json')
                zh_path = item.folder / 'i18n' / 'zh.json'
                disk_hash = sha(zh_path.read_bytes()) if zh_path.exists() else ''
                disk_changed = disk_state.exists() and read_jsonc(disk_state).get('sha') != disk_hash
                if state.exists():
                    previous = Project.load(state)
                    # Respect actual on-disk edits, while retaining cached responses
                    # which have never been installed (and therefore are absent).
                    old_keys = {key.casefold(): key for key in previous.entries}
                    for key, fresh in project.entries.items():
                        old_key = old_keys.get(key.casefold())
                        old = previous.entries.get(old_key)
                        if disk_changed and old and old.source == fresh.source and fresh.target and fresh.target != old.target:
                            previous.edit(old_key, fresh.target)
                    previous.merge_update(project)
                    project = previous
                project.save(state)
                atomic_write(disk_state, json.dumps({'sha': disk_hash}).encode('utf-8'))
                pending = [(key, entry.source) for key, entry in project.entries.items()
                           if entry.needs_translation() or (entry.needs_review and entry.origin != 'manual' and not unsupported_reason(entry.source))]
                for batch in batches(pending):
                    if stop_event.is_set():
                        raise ProjectError('用户停止，已保存成功批次，未安装此 mod')
                    responses = translator.translate(batch, project.glossary)
                    for key, source, target in responses:
                        issues = project.accept_ai(key, source, target)
                        if issues:
                            raise ProjectError('译文检查失败：' + key)
                        entry = project.entries[key]
                        # Batch mode accepts new source candidates after structural checks,
                        # but unchanged English and glossary warnings still block installation.
                        from .core import glossary_warnings
                        entry.needs_review = target.strip() == source.strip() or bool(glossary_warnings(source, target, project.glossary))
                    project.save(state)
                if stop_event.is_set():
                    raise ProjectError('用户停止，未安装此 mod')
                details = install(item, project, mods, storage / 'backups' / run_id / str(index))
                result.update(details)
                atomic_write(disk_state, json.dumps({'sha': sha(zh_path.read_bytes())}).encode('utf-8'))
                if details['skipped']:
                    reasons = '；'.join(dict.fromkeys(s['reason'] for s in details['skipped']))
                    result.update(status='部分完成', reason='已安装 %d 项；%d 项未译：%s' % (details['installed'], len(details['skipped']), reasons))
                else:
                    result.update(status='成功', reason='中文已安装；请用 SMAPI 启动，并将游戏语言设为中文')
            except ProjectError as exc:
                result['reason'] = str(exc)
                if any(code in str(exc) for code in ('（401）', '（403）', '（404）', '（429）')):
                    fatal = str(exc)
            except OSError:
                result['reason'] = '文件读写失败，请检查权限、磁盘空间或文件是否被占用；未完成的 mod 可重新扫描重试'
            except Exception:
                # Do not leak provider payloads/credentials into durable reports.
                result['reason'] = '处理遇到异常，未完成安装；已保存的翻译进度可重试'
            report['success' if result['status'] == '成功' else 'failure'].append(result)
            atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
            notify(result)
    text = ['ModLingo 批量结果', '成功名单（%d）' % len(report['success'])]
    text.extend(item['name'] + '：' + item['reason'] for item in report['success'])
    text.append('失败或部分完成名单（%d）' % len(report['failure']))
    text.extend(item['name'] + '：' + item['reason'] for item in report['failure'])
    atomic_write(report_path.with_suffix('.txt'), '\n'.join(text).encode('utf-8'))
    return report_path, report
