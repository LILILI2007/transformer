"""Offline copy-only acceptance: no credentials, no live transport, no player writes."""
import argparse
import asyncio
import cProfile
import csv
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
import time
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from modlingo import __version__
from modlingo.batch import check_game_stopped
from modlingo.discovery import scan_mods, fingerprint
from modlingo.engine import Engine
from modlingo.provider import APIConfig


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--game-path',required=True)
    parser.add_argument('--data-path',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--reuse-copy',action='store_true')
    args=parser.parse_args()
    check_game_stopped()
    source=Path(args.game_path)/'Mods';output=Path(args.output).resolve()
    output.mkdir(parents=True,exist_ok=args.reuse_copy)
    before=scan_mods(source)
    mods=output/'Mods';started=time.monotonic()
    if not args.reuse_copy:shutil.copytree(source,mods)
    elif not mods.is_dir():raise SystemExit('Missing isolated copy')
    storage=output/'cache';storage.mkdir(exist_ok=args.reuse_copy)
    dbpath=(Path(args.data_path)/'translations.sqlite3').resolve()
    with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as original:
        with sqlite3.connect(storage/'translations.sqlite3') as copy:
            original.backup(copy)
            rows=copy.execute('SELECT folder,context,source,target FROM baseline').fetchall()
            copy.executemany('INSERT OR REPLACE INTO baseline VALUES (?,?,?,?)',
                ((str(mods/Path(folder).relative_to(source)),context,raw,target) for folder,context,raw,target in rows if Path(folder).is_relative_to(source)))
    # Migration files refer to the player's folder; use the SQLite snapshot only.
    items=[item for item in scan_mods(mods) if item.mod_id!='ModLingo.Bridge']
    async def forbidden(request):raise AssertionError('Offline audit attempted network')
    config=APIConfig('https://api.deepseek.com','deepseek-flash',provider='deepseek',disable_thinking=True)
    profile=cProfile.Profile();profile.enable()
    report_path,report=asyncio.run(Engine(items,mods,config,threading.Event(),storage,
        use_processes=False,offline_only=True,transport=httpx.MockTransport(forbidden)).run())
    profile.disable();profile.dump_stats(str(output/'planning.prof'))
    # Verify the watched player files were not changed by audit or concurrent work.
    changed=[item.mod_id for item in before if item.snapshot and fingerprint(item.folder)!=item.snapshot]
    rows=[]
    for result in report['success']+report['failure']:
        coverage=result.get('coverage',{})
        total=coverage.get('total_records',0);complete=coverage.get('qualified_records',coverage.get('completed_records',0))
        rows.append(dict(mod_id=result['mod_id'],name=result['name'],status=result['status'],
                         completed_records=complete,total_records=total,
                         extracted_coverage=round(complete/total,5) if total else None,
                         installed_in_copy=result.get('installed',0),
                         pending_capture_fields=coverage.get('pending_capture_fields',0),
                         by_adapter=coverage.get('by_adapter',{}),runtime_verified=False,
                         quality_review_items=len(result.get('quality_warnings',[])),
                         unknown_dynamic_coverage=True,reason=result['reason']))
    summary=dict(schema_version=3,version=__version__,created=datetime.now().astimezone().isoformat(),
                 mods=len(items),mode='offline_existing_cache_copy_only',requests=report['metrics']['requests'],
                 player_files_changed=changed,elapsed_seconds=round(time.monotonic()-started,3),
                 timings=report['timings'],counts=report['counts'],rows=rows,
                 full_library_acceptance=False,cold_600_seconds_verified=False)
    (output/'coverage.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    with (output/'coverage.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['名称','UniqueID','状态','合格记录','提取记录','比例','隔离安装记录','待采集字段','游戏验证','原因'])
        for row in rows:writer.writerow([row['name'],row['mod_id'],row['status'],row['completed_records'],row['total_records'],row['extracted_coverage'],row['installed_in_copy'],row['pending_capture_fields'],'未验证',row['reason']])
    print(json.dumps({k:v for k,v in summary.items() if k!='rows'},ensure_ascii=True))
    if changed or summary['requests']:raise SystemExit('Audit safety invariant failed')
    if report.get('paused_reason'):raise SystemExit('Audit did not finish; inspect report')


if __name__=='__main__':main()
