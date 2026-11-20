"""Publish only aggregate local evidence, never captures or player text dumps."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from modlingo import __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit', required=True)
    args = parser.parse_args()
    output = ROOT / 'artifacts'
    audit = Path(args.audit)
    coverage = json.loads((audit / 'coverage.json').read_text(encoding='utf-8'))
    probe = json.loads((output / f'game-probe-{__version__}.json').read_text(encoding='utf-8'))
    menus = {row['mod_id']: row for row in probe.get('real_menu_results', [])}
    names = lambda mod: {row.get('name') for row in menus.get(mod, {}).get('options', [])}
    checks = {
        'no_pam_labels': {'任务来信寄件人', '调试：强制发送新的任务来信'} <= names('recon88.NoPamEnabling'),
        'seed_sections': {'样式设置', '全局开关', '模组分类开关', '原版内容'} <= names('7eventy7.StandardizedSeedSprites'),
    }
    choices = {(c['raw'], c['display']) for row in menus.get('jibb.minecarts', {}).get('options', [])
               for c in row.get('choices') or []}
    checks['minecart_known_choices'] = {('Bus', '巴士站'), ('Town', '鹈鹕镇'), ('Mines', '矿井'), ('Unused', '未使用')} <= choices
    # Values in the copy must remain byte-identical after the isolated probe.
    from modlingo.core import read_jsonc
    source_configs = {}
    for manifest in (audit / 'Mods').rglob('manifest.json'):
        data = read_jsonc(manifest)
        if data.get('UniqueID') in menus:
            path = manifest.parent / 'config.json'
            source_configs[data['UniqueID']] = path.read_bytes() if path.exists() else None
    config_checks = {}
    for manifest in (Path(probe['profile']) / 'Mods').glob('Real-*/manifest.json'):
        data = read_jsonc(manifest)
        path = manifest.parent / 'config.json'
        config_checks[data['UniqueID']] = source_configs.get(data['UniqueID']) == (path.read_bytes() if path.exists() else None)
    summary = {k: v for k, v in probe.items() if k not in ('real_menu_results', 'profile', 'numeric_first', 'numeric_second')}
    summary.update(schema_version=1, version=__version__, known_menu_checks=checks,
                   config_bytes_unchanged=config_checks, visual_layout_verified=False,
                   all_mod_dynamic_content_verified=False)
    (output / f'game-probe-summary-{__version__}.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    for extension in ('json', 'csv'):
        shutil.copy2(audit / f'coverage.{extension}', output / f'coverage-{__version__}.{extension}')
    performance = {k: coverage[k] for k in ('schema_version', 'version', 'created', 'mode', 'mods', 'elapsed_seconds', 'timings', 'requests', 'player_files_changed', 'cold_600_seconds_verified')}
    performance['note'] = '已有译文的温副本；不是首次冷缓存或模型服务性能。'
    (output / f'performance-{__version__}.json').write_text(json.dumps(performance, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'known_menu_checks': checks, 'config_bytes_unchanged': config_checks,
                      'qualified_records': sum(r['completed_records'] for r in coverage['rows']),
                      'identified_records': sum(r['total_records'] for r in coverage['rows'])}))
    if not all(checks.values()) or len(config_checks) != 3 or not all(config_checks.values()):
        raise SystemExit('Known menu or config checks failed; do not mark verified.')


if __name__ == '__main__':
    main()
