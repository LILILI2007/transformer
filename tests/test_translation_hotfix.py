import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import httpx
from modlingo.async_provider import Session,translate_recover
from modlingo.batching import fragment_groups,fits,wire_batches
from modlingo.core import validate_translation
from modlingo.discovery import scan_mods
from modlingo.engine import Engine
from modlingo.provider import APIConfig,Translator,ModelResponseError

def translated(request):
    items=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
    output={key:row['text'].replace('Hello','你好').replace('Welcome','欢迎').replace('Goodbye','再见').replace('Sir','先生').replace('Madam','女士') for key,row in items.items()}
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output)}}]}

class HotfixTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mods=self.root/'Mods';self.mods.mkdir()
        self.config=APIConfig('http://127.0.0.1:3333/v1','contract')

    def mod(self,sources):
        folder=self.mods/'Fixture';(folder/'i18n').mkdir(parents=True)
        (folder/'manifest.json').write_text('{"UniqueID":"Test.Fixture","Name":"Fixture","Version":"1"}',encoding='utf-8')
        (folder/'i18n/default.json').write_text(json.dumps(sources),encoding='utf-8')
        return folder

    def run_engine(self,handler):
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            engine=Engine(scan_mods(self.mods),self.mods,self.config,threading.Event(),self.root/'cache',transport=httpx.MockTransport(handler),use_processes=False)
            path,report=asyncio.run(engine.run())
            return path,report

    def test_original_split_marker_collision_is_reproduced_and_fixed(self):
        translator=Translator(self.config)
        for source in ('Hello {{__ML_SPLIT_test_1}}Welcome','Hello __ML_literal_name'):
            items=[('key',source)]
            _,prefix,records,restores=translator.prepare(items,{})
            answer=records['0']['text'].replace('Hello','你好').replace('Welcome','欢迎')
            results,errors=translator.decode(json.dumps({'0':answer}),items,prefix,records,restores,partial=True)
            self.assertEqual(errors,{})
            self.assertEqual(results[0][2],source.replace('Hello','你好').replace('Welcome','欢迎'))

    def test_forged_missing_and_reordered_markers_still_rejected(self):
        translator=Translator(self.config);items=[('key','Hello {{Name}}#Welcome')]
        _,prefix,records,restores=translator.prepare(items,{})
        good=records['0']['text'].replace('Hello','你好').replace('Welcome','欢迎')
        first,second=[m for m,_ in restores['0']]
        for bad in (good+'__ML_unknown__',good.replace(first,''),good.replace(first,'TEMP').replace(second,first).replace('TEMP',second)):
            results,errors=translator.decode(json.dumps({'0':bad}),items,prefix,records,restores,partial=True)
            self.assertFalse(results);self.assertTrue(errors)

    def test_complex_realistic_pipeline_installs_without_retry(self):
        sources={str(i):source for i,source in enumerate([
            'Hello {{PlayerName}}!#Welcome^Goodbye.$h',
            'Hello\nWelcome {{PlayerName}}! Goodbye.',
            '${Sir^Madam}$, Welcome!',
            '$q 1/2 branch#Hello#$r 1 0 yes#Welcome',
            'Hello %item object 74 1 %%[#]Welcome',
            'pause 100/speak Abigail "Hello / Welcome {{PlayerName}}!"/end',
            'pause 100/speak Abigail "Hello $7#Welcome $10"/end',
            'pause 100/speak Abigail "$q 200 null#Hello#$r 200 50 event_secret1#Welcome"/end',
        ]*10)}
        folder=self.mod(sources);calls=[]
        async def handler(request):
            calls.append(request);return httpx.Response(200,json=translated(request))
        path,report=self.run_engine(handler)
        self.assertEqual(report['failure'],[])
        self.assertEqual(report['metrics']['failed'],0)
        self.assertLessEqual(len(calls),5) # isolated text kinds form separate batches
        pack=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))
        for key,source in sources.items():
            self.assertFalse(validate_translation(source,pack[key]))
            self.assertIn('欢迎',pack[key])
        previous=len(calls);self.run_engine(handler)
        self.assertEqual(len(calls),previous)
        diagnostics=path.with_name(path.stem+'.requests.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('PlayerName',diagnostics);self.assertNotIn('Authorization',diagnostics)

    def test_fragment_joining_counts_separator_overhead(self):
        groups=list(fragment_groups([(str(i),'Hello Welcome') for i in range(1000)],600,1024))
        self.assertGreater(len(groups),1)
        for chunk,marker,joined in groups:
            self.assertTrue(fits(joined,600,1024));self.assertLessEqual(len(chunk),20)

    def test_output_limit_shrinks_future_batches_and_retains_good_items(self):
        async def run():
            counts=[];results=[];errors=[]
            async def handler(request):
                rows=json.loads(json.loads(request.content)['messages'][-1]['content'])['items'];counts.append(len(rows))
                if len(rows)>5:return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{"0":"cut'}}]})
                return httpx.Response(200,json=translated(request))
            async with Session(self.config,transport=httpx.MockTransport(handler)) as session:
                await translate_recover(session,[(str(i),'Hello '+str(i)) for i in range(20)],{},lambda rows,usage:results.extend(rows),lambda rows,error:errors.extend(rows))
                self.assertEqual(session.batch_items,10);self.assertLessEqual(session.requests,8)
            self.assertEqual(len(results),20);self.assertEqual(errors,[])
            self.assertEqual(counts,[20,5,5,5,5])
        asyncio.run(run())

    def test_refusal_does_not_repeat_eight_requests(self):
        async def run():
            errors=[]
            async def handler(request):
                return httpx.Response(200,json={'choices':[{'finish_reason':'content_filter','message':{'content':None}}]})
            async with Session(self.config,transport=httpx.MockTransport(handler)) as session:
                await translate_recover(session,[(str(i),'Hello') for i in range(20)],{},lambda rows,usage:None,lambda rows,error:errors.extend(rows))
                self.assertEqual(session.requests,1)
            self.assertEqual(len(errors),20)
        asyncio.run(run())

    def test_high_failure_rate_pauses_pending_paid_work(self):
        self.mod({str(i):'Hello '+str(i) for i in range(400)})
        async def handler(request):
            return httpx.Response(200,json={'choices':[{'message':{'content':'broken JSON'}}]})
        path,report=self.run_engine(handler)
        self.assertNotIn('paused_reason',report)
        self.assertTrue(report['scope_pauses'])
        self.assertLessEqual(report['metrics']['requests'],32)
        self.assertEqual(report['metrics']['eta_seconds'],0)

    def test_eta_excludes_preparation_and_terminal_failures(self):
        engine=Engine([],self.mods,self.config,threading.Event(),self.root/'cache',use_processes=False)
        engine.started=time.monotonic()-3600;engine.translation_started=time.monotonic()-60
        engine.metrics.update(total=1000,completed=200,cache_hits=100,failed=200)
        engine.emit();self.assertEqual(engine.metrics['eta_seconds'],120)
        engine.metrics.update(completed=600,failed=400);engine.emit()
        self.assertEqual(engine.metrics['eta_seconds'],0)
        engine.translation_paused=True;engine.emit();self.assertIsNone(engine.metrics['eta_seconds'])

    def test_oversized_single_fragment_makes_no_paid_request(self):
        async def run():
            errors=[]
            async def handler(request):raise AssertionError('must not send')
            async with Session(self.config,transport=httpx.MockTransport(handler)) as session:
                await translate_recover(session,[('a','Hello '*5000)],{},lambda rows,usage:None,lambda rows,error:errors.append(error))
                self.assertEqual(session.requests,0)
            self.assertIn('超过当前安全输出预算',errors[0])
        asyncio.run(run())
