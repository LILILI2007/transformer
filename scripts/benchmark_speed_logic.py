"""Offline proof of adaptive recovery and conservative full-field deduplication."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import httpx
from modlingo import __version__
from modlingo.adaptive import FairQueue
from modlingo.async_provider import Session,translate_recover
from modlingo.discovery import scan_mods
from modlingo.engine import Engine
from modlingo.provider import APIConfig

CONFIG=APIConfig('http://127.0.0.1:3333/v1','offline')

async def recovery(adaptive):
    counts=[];completed=[];errors=[]
    async def handler(request):
        rows=json.loads(json.loads(request.content)['messages'][-1]['content'])['items'];counts.append(len(rows))
        await asyncio.sleep(.005)
        output={key:'中文'+row['text'] for key,row in rows.items()}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output)}}]})
    started=time.perf_counter()
    async with Session(CONFIG,concurrency=1,transport=httpx.MockTransport(handler)) as session:
        items=[(str(i),'Hello farmer '+str(i)) for i in range(500)]
        session.adaptive.shrink(('default','short'),2,'synthetic prior shrink')
        jobs=FairQueue([items])
        while jobs:
            _,batch=jobs.pop(session)
            await translate_recover(session,batch,{},lambda rows,usage:completed.extend(rows),lambda rows,error:errors.extend(rows))
            if not adaptive:session.adaptive.get(('default','short')).limit=1
    return {'requests':len(counts),'entries':len(completed),'errors':len(errors),'largest_batch':max(counts),
            'elapsed_seconds':round(time.perf_counter()-started,3)}

async def deduplication(root):
    folder=root/'Mods'/'Synthetic';(folder/'i18n').mkdir(parents=True)
    (folder/'manifest.json').write_text('{"UniqueID":"Test.Synthetic","Name":"Synthetic","Version":"1"}')
    sources={'dialogue.A.'+str(i):'Hello farmer, welcome to this beautiful valley number '+str(i%25)+'.' for i in range(1000)}
    (folder/'i18n/default.json').write_text(json.dumps(sources))
    async def handler(request):
        rows=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
        output={key:'中文'+row['text'] for key,row in rows.items()}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output)}}]})
    with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
        engine=Engine(scan_mods(root/'Mods'),root/'Mods',CONFIG,threading.Event(),root/'cache',transport=httpx.MockTransport(handler),use_processes=False)
        _,report=await engine.run()
    return {'plan':report['plan'],'requests':report['metrics']['requests'],'success_mods':len(report['success'])}

async def main():
    fixed=await recovery(False);adaptive=await recovery(True)
    with tempfile.TemporaryDirectory() as temp:dedup=await deduplication(Path(temp))
    result={'version':__version__,'mode':'synthetic MockTransport; no paid API / player files',
            'fixed_single_item':fixed,'adaptive_recovery':adaptive,'deduplication_fixture':dedup}
    result['passed']=adaptive['requests']<fixed['requests']/5 and adaptive['entries']==500 and adaptive['errors']==0 and dedup['plan']['pending_fields']==25 and dedup['success_mods']==1
    path=Path(__file__).resolve().parent.parent/('artifacts/speed-audit-'+__version__+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))
    if not result['passed']:raise SystemExit(1)

if __name__=='__main__':asyncio.run(main())
