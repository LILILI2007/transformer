"""Synthetic offline benchmark. MockTransport never contacts a model service."""
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
from modlingo.engine import Engine
from modlingo.discovery import scan_mods
from modlingo.provider import APIConfig
from modlingo import __version__

async def bench(count,concurrency,delay,root,structured=False):
    mods=root/'Mods';folder=mods/'Synthetic';(folder/'i18n').mkdir(parents=True)
    (folder/'manifest.json').write_text('{"UniqueID":"Benchmark.Synthetic","Name":"Synthetic","Version":"1"}',encoding='utf-8')
    (folder/'i18n/default.json').write_text(json.dumps({str(i):'Hello farmer '+str(i)+(' {{PlayerName}}!#Welcome^Goodbye.$h' if structured else '') for i in range(count)}),encoding='utf-8')
    current=peak=requests=0
    async def handler(request):
        nonlocal current,peak,requests
        current+=1;requests+=1;peak=max(peak,current)
        try:
            await asyncio.sleep(delay)
            payload=json.loads(request.content)
            items=json.loads(payload['messages'][-1]['content'])['items']
            output={key:row['text'].replace('Hello farmer','你好农夫').replace('Welcome','欢迎').replace('Goodbye','再见') for key,row in items.items()}
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output)}}]})
        finally:
            current-=1
    events=[]
    config=APIConfig('http://127.0.0.1:3333/v1','synthetic')
    with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
        engine=Engine(scan_mods(mods),mods,config,threading.Event(),root/'cache',concurrency,
                      lambda kind,data:events.append(time.monotonic()) if kind=='engine_progress' else None,
                      httpx.MockTransport(handler),use_processes=False)
        start=time.perf_counter();_,report=await engine.run();elapsed=time.perf_counter()-start
        before=requests
        second=Engine(scan_mods(mods),mods,config,threading.Event(),root/'cache',concurrency,transport=httpx.MockTransport(handler),use_processes=False)
        start=time.perf_counter();await second.run();cached=time.perf_counter()-start
    return {'entries':count,'structured':structured,'concurrency':concurrency,'fixed_response_delay_seconds':delay,'elapsed_seconds':round(elapsed,3),
            'requests':before,'peak_in_flight':peak,'progress_events':len(events),'cache_rerun_seconds':round(cached,3),'cache_rerun_requests':requests-before,
            'success_mods':len(report['success'])}

async def main():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        serial=await bench(1000,1,.1,root/'serial')
        concurrent=await bench(1000,4,.1,root/'concurrent')
        stress=await bench(10000,4,.005,root/'stress')
        structured=await bench(2000,4,.005,root/'structured',True)
    ratio=serial['elapsed_seconds']/concurrent['elapsed_seconds']
    result={'version':__version__,'network':'MockTransport only; no real API or player files','serial':serial,'concurrent':concurrent,'speedup':round(ratio,2),'stress_10000':stress,'structured_2000':structured,
            'passed':ratio>=2.5 and concurrent['peak_in_flight']<=4 and stress['success_mods']==1 and stress['cache_rerun_requests']==0 and structured['success_mods']==1 and structured['cache_rerun_requests']==0 and structured['requests']==100}
    path=Path(__file__).resolve().parent.parent/('artifacts/performance-'+__version__+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))
    if not result['passed']:raise SystemExit(1)

if __name__=='__main__':asyncio.run(main())
