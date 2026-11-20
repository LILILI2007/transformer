"""Only authorized, durable-budget live runner. Never write player Mods."""
import argparse
import asyncio
import hashlib
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import sys
import threading
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from modlingo import __version__
from modlingo.async_provider import Session,Budget,ServiceError,translate_recover
from modlingo.adaptive import FairQueue
from modlingo.bridge_io import captures
from modlingo.core import atomic_write,ProjectError
from modlingo.discovery import scan_mods,within
from modlingo.engine import Engine
from modlingo.extraction import extract
from modlingo.provider import Translator,preset_config
from modlingo.test_budget import TestLedger,BudgetStop,DEADLINE
from modlingo.test_credentials import load,clear
from modlingo.test_pricing import verify_prices
from modlingo.test_snapshot import freeze_sources,verify_snapshot
from modlingo.memory import whole_text
from modlingo.test_preflight import forecast
from modlingo.test_structures import select_fields,select_failed_fields,run_fields

ROOT=Path(r'D:\chatgpt工作区\ModLingo-限额实测')
PLAYER_MODS=Path(r'E:\SteamLibrary\steamapps\common\Stardew Valley\Mods')

def save(path,value):atomic_write(path,json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8'))

def code_fingerprint():
    root=Path(__file__).resolve().parent.parent
    digest=hashlib.sha256()
    for path in sorted(list((root/'modlingo').glob('*.py'))+[Path(__file__).resolve()]):
        digest.update(path.relative_to(root).as_posix().encode('utf-8'))
        digest.update(b'\0');digest.update(path.read_bytes())
    return digest.hexdigest()

def sample(snapshot,count):
    records=[];offline=captures(snapshot/'Mods');coverage=[]
    for item in scan_mods(snapshot/'Mods'):
        if not item.mod_id:continue
        try:
            bundle=extract(item,offline)
            coverage.append({'mod_id':item.mod_id,'category':bundle.category,'units':len(bundle.units),'notes':bundle.notes})
            for record in bundle.records:
                if record.target or record.notes or not record.parts or not whole_text(record):continue
                if not 10<=len(record.source)<=500:continue
                if not any('A'<=c<='Z' or 'a'<=c<='z' for c in record.source):continue
                records.append((item.mod_id+' | '+record.context,record.source))
        except (ProjectError,OSError,ValueError) as exc:
            coverage.append({'mod_id':item.mod_id,'category':'文件异常','notes':[str(exc)]})
    if not records:raise ProjectError('没有可校准的原文')
    step=max(1,len(records)//count)
    return records[::step][:count],coverage

async def calibration(config,round,records,concurrency,output):
    diagnostics=[];results=[];errors=[];peaks=[]
    async with Session(config,concurrency,test_budget=round,on_trace=diagnostics.append,on_stats=lambda stats:peaks.append(stats['in_flight'])) as session:
        verification=[('verify','Hello, {{PlayerName}}!\nWelcome to the farm!')]
        translator=Translator(config)
        messages,prefix,inputs,restores=translator.prepare(verification,{})
        response=await session.post(translator,messages,Budget())
        verified=translator.decode(response,verification,prefix,inputs,restores)
        if not any('\u4e00'<=c<='\u9fff' for c in verified[0][2]):raise ProjectError('API 验证未返回中文')
        items=[(str(i),source) for i,(context,source) in enumerate(records)]
        session.contexts={str(i):context for i,(context,source) in enumerate(records)}
        queue=FairQueue([items]);active=set()
        started=time.monotonic()
        try:
            while queue or active:
                round.check()
                while queue and len(active)<session.limit:
                    _,batch=queue.pop(session)
                    active.add(asyncio.create_task(translate_recover(session,batch,{},lambda rows,usage:results.extend(rows),lambda rows,error:errors.extend({'key':key,'reason':error} for key,source in rows))))
                done,active=await asyncio.wait(active,timeout=.1,return_when=asyncio.FIRST_COMPLETED)
                for task in done:task.result()
        finally:
            for task in active:task.cancel()
            await asyncio.gather(*active,return_exceptions=True)
            save(output/'http.json',diagnostics)
            save(output/'sample-results.json',{'results':results,'errors':errors})
        latencies=sorted(row['elapsed_ms']/1000 for row in diagnostics if row['event']=='http')
        return {'kind':'calibration_not_full_acceptance','concurrency':concurrency,'sample_records':len(records),
                'wire_profile':'deepseek-compact-v1',
                'completed':len(results),'failed':len(errors),'elapsed_seconds':round_time(time.monotonic()-started),
                'requests':session.requests,'all_sample_passed':len(results)==len(records) and not errors,
                'peak_in_flight':max(peaks,default=0),'latency_p50_seconds':latencies[len(latencies)//2] if latencies else None,
                'latency_p95_seconds':latencies[min(len(latencies)-1,int(len(latencies)*.95))] if latencies else None,
                'game_verified':False,'cold_full_acceptance':False}

def round_time(value):return round(value,3)

async def cold(config,round,snapshot,output,concurrency):
    mods=output/'Mods'
    shutil.copytree(snapshot/'Mods',mods)
    if not within(mods,ROOT) or within(mods,PLAYER_MODS):raise ProjectError('拒绝向玩家目录运行验收安装')
    began=time.monotonic()
    items=[item for item in scan_mods(mods) if item.mod_id]
    async with Session(config,1,test_budget=round) as session:
        translator=Translator(config);items_to_verify=[('verify','Hello, {{PlayerName}}!')]
        messages,prefix,inputs,restores=translator.prepare(items_to_verify,{})
        response=await session.post(translator,messages,Budget())
        verified=translator.decode(response,items_to_verify,prefix,inputs,restores)
        if not any('\u4e00'<=c<='\u9fff' for c in verified[0][2]):raise ProjectError('本轮 API 验证未返回中文')
    # All writes are confined to a private cold fixture. The actual game need not
    # be stopped merely to translate an isolated fixture, but is never altered.
    with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
        engine=Engine(items,mods,config,threading.Event(),output/'empty-cache',concurrency=concurrency,test_budget=round)
        path,report=await engine.run()
    elapsed=time.monotonic()-began
    failures=[row for row in report['failure'] if row['status']!='无需翻译']
    return {'kind':'cold_full_acceptance','report_path':str(path),'elapsed_seconds':round_time(elapsed),
            'counts':report.get('counts',{}),'metrics':report.get('metrics',{}),'coverage_passed':not failures,
            'speed_passed':elapsed<=600 and not failures and not report.get('paused_reason'),
            'game_verified':False,'overall_passed':False,'note':'静态冷缓存测试不代替未访问动态界面的游戏验收。'}

async def compare(config,round,records,output):
    levels=[]
    for concurrency in (4,8,16,32):
        folder=output/('level-'+str(concurrency));folder.mkdir()
        before=round.snapshot()['conservative_cny']
        result=await calibration(config,round,records,concurrency,folder)
        result['conservative_cost_delta_cny']=round.snapshot()['conservative_cny']-before
        levels.append(result);save(output/'compare-progress.json',levels)
        if not result['all_sample_passed'] or result['latency_p95_seconds']>max(30,levels[0]['latency_p95_seconds']*2):break
    return {'kind':'same_sources_concurrency_comparison','levels':levels,'cold_full_acceptance':False,'overall_passed':False}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=['calibration','compare','preflight','cold','structures'],default='calibration')
    parser.add_argument('--concurrency',type=int,choices=[4,8,16,32],default=4)
    parser.add_argument('--sample',type=int,default=128);parser.add_argument('--seconds',type=int,default=1200)
    parser.add_argument('--failed-round',type=int)
    args=parser.parse_args()
    if not 1<=args.sample<=1000 or not 0<args.seconds<=1200:raise SystemExit('测试参数超出限额')
    if args.stage=='structures' and args.sample>64:raise SystemExit('复杂字段实测最多64个记录')
    if args.failed_round is not None and args.stage!='structures':raise SystemExit('失败集复验只用于复杂字段阶段')
    ROOT.mkdir(parents=True,exist_ok=True)
    ledger=TestLedger(ROOT/'budget.sqlite3');round=None;output=None;result={};started=time.monotonic()
    try:
        if time.time()>=DEADLINE:
            clear();raise BudgetStop('已到当日截止时间；专用凭据已清除')
        if args.stage!='preflight' and ledger.snapshot()['rounds']>=ledger.maximum:
            raise BudgetStop('已达到最多5轮付费测试上限；不读取凭据，不再发送API请求')
        pricing=verify_prices()
        snapshot=ROOT/'source-snapshot'
        if not snapshot.exists():freeze_sources(PLAYER_MODS,snapshot)
        inventory=verify_snapshot(snapshot)
        if args.stage in ('preflight','cold'):
            prediction=forecast(snapshot,ROOT)
            save(ROOT/'preflight.json',prediction)
            if args.stage=='preflight':
                result=prediction
                return 0
            if not prediction['cold_acceptance_allowed']:
                raise BudgetStop('全量冷缓存验收预检未通过：费用预计超单轮预算或存在未覆盖文字；未启动付费轮次')
        if args.stage=='structures' and args.failed_round is not None:records,coverage=select_failed_fields(snapshot,ROOT,args.failed_round)
        elif args.stage=='structures':records,coverage=select_fields(snapshot,args.sample)
        else:records,coverage=sample(snapshot,args.sample)
        # No secret is persisted in argv, files, traces or public artifacts.
        config=preset_config('deepseek','deepseek-flash',load())
        config.max_output_tokens=4096 if args.stage!='cold' else 8192
        round=ledger.begin(args.stage+' concurrency='+str(args.concurrency),args.seconds,pricing['verified_at'])
        output=ROOT/('round-'+str(ledger.snapshot()['rounds'])+'-'+round.id[:8]);output.mkdir()
        save(output/'pricing.json',pricing);save(output/'coverage-before.json',coverage)
        save(output/'settings.json',{'version':__version__,'code_sha256':code_fingerprint(),'stage':args.stage,'concurrency':args.concurrency,'sample':args.sample,'failed_from_round':args.failed_round,
                                     'seconds':args.seconds,'round_limit_cny':3,'total_limit_cny':15,'model':'deepseek-flash','thinking_disabled':True})
        if args.stage in ('calibration','compare'):
            save(output/'sample-sources.json',records)
            operation=compare(config,round,records,output) if args.stage=='compare' else calibration(config,round,records,args.concurrency,output)
            result=asyncio.run(asyncio.wait_for(operation,round.remaining()))
        elif args.stage=='structures':result=asyncio.run(asyncio.wait_for(run_fields(config,round,records,output,args.concurrency),round.remaining()))
        else:result=asyncio.run(asyncio.wait_for(cold(config,round,snapshot,output,args.concurrency),round.remaining()))
    except Exception as exc:
        # Never print raw transport exception or server content: credentials can
        # otherwise leak from URL/header debugging. ProjectError messages are ours.
        result={'stopped':True,'reason':str(exc) if isinstance(exc,ProjectError) else type(exc).__name__,
                'overall_passed':False,'cold_full_acceptance':False}
    finally:
        if round:
            round.finish();result['budget']=round.snapshot()
        result['cumulative_budget']=ledger.snapshot();result['total_wall_seconds']=round_time(time.monotonic()-started)
        if output:save(output/'result.json',result)
        save(ROOT/'latest-result.json',result);ledger.close()
        summary=dict(result)
        if 'coverage_gaps' in summary:
            summary['coverage_gap_count']=len(summary.pop('coverage_gaps'))
        print(json.dumps(summary,ensure_ascii=True))
    return 0 if not result.get('stopped') else 1

if __name__=='__main__':raise SystemExit(main())
