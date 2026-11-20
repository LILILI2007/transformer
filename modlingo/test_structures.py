"""Small cold fixtures from pinned original fields, never whole-library acceptance."""
import asyncio
from collections import Counter, defaultdict
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch
from .async_provider import Session, Budget
from .bridge_io import captures
from .core import ProjectError, atomic_write
from .discovery import scan_mods, within
from .engine import Engine
from .extraction import extract
from .provider import Translator
from .syntax import has_quick_question
from .test_snapshot import verify_snapshot


def save(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))


def select_fields(snapshot, limit=16):
    if type(limit) is not int or not 1 <= limit <= 64:
        raise ProjectError('复杂字段测试最多选择64个记录')
    snapshot = Path(snapshot)
    verify_snapshot(snapshot)
    pools = defaultdict(list)
    excluded = Counter()
    seen = set()
    offline = captures(snapshot/'Mods')
    for item in scan_mods(snapshot/'Mods'):
        if not item.mod_id:
            continue
        try:
            bundle = extract(item, offline)
        except (ProjectError, ValueError, OSError):
            excluded['file_error'] += 1
            continue
        for record in bundle.records:
            category = ('quick_question' if has_quick_question(record.source) else
                        'quest' if record.kind == 'quest' else
                        'mail' if '%item' in record.source else
                        'dialogue_branch' if '$q ' in record.source else '')
            if not category:
                continue
            # Only exact top-level dictionary fields are materialized here.
            # Conditions remain a game-side acceptance task, not flattened proof.
            if record.adapter not in ('asset', 'i18n') or (record.adapter == 'asset' and len(record.path) != 1):
                excluded['not_top_level_asset'] += 1
                continue
            if record.notes or not record.parts:
                excluded['unsupported_structure'] += 1
                continue
            if len(record.source) > 6000:
                excluded['sample_size_limit'] += 1
                continue
            if not any('A' <= c <= 'Z' or 'a' <= c <= 'z' for p in record.parts for c in p.text):
                continue
            identity = (item.mod_id, record.adapter, record.context, record.asset, tuple(record.path), record.source)
            if identity in seen:
                continue
            seen.add(identity)
            pools[category].append({'mod_id': item.mod_id, 'name': item.name,
                                    'version': item.version, 'category': category,
                                    'adapter': record.adapter, 'asset': record.asset, 'path': record.path,
                                    'source': record.source, 'context': record.context,
                                    'fragments': len(record.parts)})
    selected = []
    order = ('quick_question', 'quest', 'mail', 'dialogue_branch')
    # Stable, balanced sample; no translations or previous paid cache consulted.
    index = 0
    while len(selected) < limit:
        added = False
        for category in order:
            if index < len(pools[category]):
                selected.append(pools[category][index])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        index += 1
    if not selected or not any(r['category'] == 'quick_question' for r in selected):
        raise ProjectError('固定原文快照没有可验证的新快速问答字段；未启动付费轮次')
    return selected, {'candidate_counts': {k: len(v) for k,v in pools.items()},
                      'sample_counts': dict(Counter(r['category'] for r in selected)),
                      'excluded_counts': dict(excluded), 'full_coverage': False}


def prepare_fields(fields, destination):
    destination = Path(destination)
    if destination.exists():
        raise ProjectError('复杂字段测试目录必须全新，拒绝覆盖或复用缓存')
    grouped = defaultdict(list)
    identities = set()
    for row in fields:
        adapter = row.get('adapter', 'asset')
        if (not isinstance(row.get('source'), str) or adapter not in ('asset', 'i18n') or
            (adapter == 'asset' and (not row.get('asset') or len(row.get('path', [])) != 1)) or
            (adapter == 'i18n' and not isinstance(row.get('context'), str))):
            raise ProjectError('复杂字段来源结构无效')
        identity = (row['mod_id'], adapter, row.get('context'), row['asset'], tuple(row['path']), row['source'])
        if identity in identities:
            raise ProjectError('复杂字段测试集包含重复记录')
        identities.add(identity)
        grouped[row['mod_id']].append(row)
    destination.mkdir(parents=True)
    for index, rows in enumerate(grouped.values()):
        folder = destination/str(index)
        folder.mkdir()
        first = rows[0]
        save(folder/'manifest.json', {'UniqueID': first['mod_id'], 'Name': first['name'],
             'Version': first['version'], 'Author': 'ModLingo isolated test fixture',
             'ContentPackFor': {'UniqueID': 'Pathoschild.ContentPatcher'}})
        save(folder/'content.json', {'Format': '2.0.0', 'Changes': [
             {'Action': 'EditData', 'Target': r['asset'], 'Entries': {r['path'][0]: r['source']}}
             for r in rows if r.get('adapter', 'asset') == 'asset']})
        i18n = [r for r in rows if r.get('adapter') == 'i18n']
        if i18n:
            if len({r['context'] for r in i18n}) != len(i18n):
                raise ProjectError('i18n测试字段键重复，拒绝覆盖')
            save(folder/'i18n/default.json', {r['context']: r['source'] for r in i18n})
    return destination


def select_failed_fields(snapshot, test_root, number):
    """Revalidate failed original fields; never import old translations/cache."""
    if type(number) is not int or not 1 <= number <= 5:
        raise ProjectError('历史轮次编号无效')
    snapshot, test_root = Path(snapshot), Path(test_root)
    verify_snapshot(snapshot)
    matches = list(test_root.glob('round-'+str(number)+'-*'))
    if len(matches) != 1:
        raise ProjectError('找不到唯一的历史测试轮次')
    previous = matches[0]
    result = json.loads((previous/'result.json').read_text(encoding='utf-8'))
    if result.get('kind') != 'structured_cold_sample_not_full_acceptance':
        raise ProjectError('仅支持复杂字段失败集复验')
    report_path = Path(result['report_path'])
    if not within(report_path, previous):
        raise ProjectError('历史报告路径超出本轮目录')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    failed = {(row['mod_id'], skipped['key']) for row in report['failure']
              for skipped in row.get('skipped', [])}
    original = json.loads((previous/'structured-sources.json').read_text(encoding='utf-8'))
    items = {item.mod_id: item for item in scan_mods(snapshot/'Mods')}
    offline = captures(snapshot/'Mods')
    bundles = {}
    selected = []
    for old in original:
        if (old['mod_id'], old['context']) not in failed:
            continue
        item = items.get(old['mod_id'])
        if item is None:
            raise ProjectError('历史字段所属mod不在固定快照中')
        if item.mod_id not in bundles:
            bundles[item.mod_id] = extract(item, offline)
        records = [r for r in bundles[item.mod_id].records if
                   r.context == old['context'] and r.source == old['source'] and
                   r.adapter == old.get('adapter', 'asset') and r.asset == old['asset'] and r.path == old['path']]
        if len(records) != 1 or records[0].notes or not records[0].parts:
            raise ProjectError('历史失败字段未通过当前固定原文及结构校验')
        selected.append(dict(old, name=item.name, version=item.version,
                             fragments=len(records[0].parts)))
    if not selected:
        raise ProjectError('没有可复验的失败字段；未启动付费轮次')
    return selected, {'failed_from_round': number, 'sample_counts':dict(Counter(r['category'] for r in selected)),
                      'previous_successful_fields_not_requested':len(original)-len(selected),
                      'full_coverage':False, 'previous_paid_cache_used':False}


async def run_fields(config, budget, fields, output, concurrency=4, transport=None, use_processes=True):
    output = Path(output)
    started = time.monotonic()
    mods = prepare_fields(fields, output/'fixture-Mods')
    storage = output/'empty-cache'
    if storage.exists() or not within(mods, output):
        raise ProjectError('复杂字段测试要求隔离目录和空缓存')
    save(output/'structured-sources.json', fields)
    verification_trace = []
    try:
        async with Session(config, 1, transport=transport, test_budget=budget,
                           on_trace=verification_trace.append) as session:
            translator = Translator(config)
            items = [('verify', 'Hello, {{PlayerName}}!\nWelcome to the farm!')]
            messages, prefix, records, restores = translator.prepare(items, {})
            response = await session.post(translator, messages, Budget())
            verified = translator.decode(response, items, prefix, records, restores)
            if not any('\u4e00' <= c <= '\u9fff' for c in verified[0][2]):
                raise ProjectError('复杂字段实测验证未返回中文')
    finally:
        save(output/'verification-http.json', verification_trace)
    # These newly created fixtures are independent of the player's Mods. Only
    # their own installation guards are bypassed; no live game process is touched.
    with patch('modlingo.engine.check_game_stopped'), patch('modlingo.batch.check_game_stopped'):
        engine = Engine(scan_mods(mods), mods, config, threading.Event(), storage,
                        concurrency=concurrency, transport=transport,
                        test_budget=budget, use_processes=use_processes)
        path, report = await engine.run()
    results = report['success'] + report['failure']
    completed = sum(r.get('coverage', {}).get('completed_records', 0) for r in results)
    installed = sum(r.get('installed', 0) for r in results)
    expected_fragments = sum(r['fragments'] for r in fields)
    passed = (not report['failure'] and not report.get('paused_reason') and
              completed == len(fields) and installed == len(fields) and
              report['metrics']['total'] == expected_fragments)
    return {'kind': 'structured_cold_sample_not_full_acceptance',
            'selected_fields': len(fields), 'selected_fragments': expected_fragments,
            'categories': dict(Counter(r['category'] for r in fields)),
            'completed_fields': completed, 'installed_fields': installed, 'all_sample_passed': passed,
            'elapsed_seconds': round(time.monotonic()-started, 3),
            'verification_requests': len(verification_trace),
            'requests': report['metrics']['requests'] + len(verification_trace),
            'metrics': report['metrics'], 'timings': report.get('timings', {}),
            'counts': report.get('counts', {}), 'report_path': str(path),
            'cold_cache': True, 'cold_full_acceptance': False,
            'game_verified': False, 'overall_passed': False,
            'note': '原文小样本提取/翻译/校验/安装；不证明原补丁条件、剧情分支或全库游戏效果。'}
