import asyncio
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch
import httpx
from modlingo.adaptive import Adaptive,FairQueue
from modlingo.async_provider import Session,translate_recover
from modlingo.cache import Cache
from modlingo.discovery import scan_mods
from modlingo.engine import Engine
from modlingo.provider import APIConfig,Translator,preset_config

SOURCE='Hello farmer, welcome to this beautiful valley {{PlayerName}}!#Have a wonderful day.$h'

def output(request):
    rows=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
    text={key:row['text'].replace('Hello farmer, welcome to this beautiful valley','农夫，欢迎来到这座美丽的山谷').replace('Have a wonderful day.','祝你度过美好的一天。').replace('Hello','你好').replace('Welcome','欢迎') for key,row in rows.items()}
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(text)}}]}

class SpeedLogicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mods=self.root/'Mods';self.mods.mkdir()
        self.config=APIConfig('http://127.0.0.1:3333/v1','contract')

    def make_mod(self,sources,targets=None):
        folder=self.mods/'A';(folder/'i18n').mkdir(parents=True)
        (folder/'manifest.json').write_text('{"UniqueID":"Test.A","Name":"A","Version":"1"}')
        (folder/'i18n/default.json').write_text(json.dumps(sources),encoding='utf-8')
        if targets:(folder/'i18n/zh.json').write_text(json.dumps(targets),encoding='utf-8')
        return folder

    def run_engine(self,handler,**options):
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            engine=Engine(scan_mods(self.mods),self.mods,self.config,threading.Event(),storage=self.root/'cache',
                transport=httpx.MockTransport(handler),use_processes=False,**options)
            return asyncio.run(engine.run())

    def test_scoped_recovery_and_stale_feedback(self):
        trace=[];adaptive=Adaptive(trace.append);scope=('A','long')
        adaptive.shrink(scope,2,'length');generation=adaptive.get(scope).generation
        self.assertEqual(adaptive.get(('B','short')).limit,20)
        adaptive.success(scope,1,generation-1)
        self.assertEqual(adaptive.get(scope).successes,0)
        for _ in range(3):adaptive.success(scope,1,generation)
        self.assertEqual(adaptive.get(scope).limit,2)
        self.assertEqual(trace[-1]['event'],'batch_grow')

    def test_queue_recombines_after_regrowth_and_stays_fair(self):
        loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
        self.addCleanup(loop.close);self.addCleanup(asyncio.set_event_loop,None)
        session=Session(self.config);session.contexts={str(i):'A | field' for i in range(30)}
        session.contexts['other']='B | field'
        session.adaptive.shrink(('A','short'),2,'length')
        jobs=FairQueue([[(str(i),'Hello') for i in range(30)],[('other','Hello')]])
        self.assertEqual(len(jobs.pop(session)[1]),1)
        self.assertEqual(jobs.pop(session)[0],1)
        state=session.adaptive.get(('A','short'))
        for _ in range(3):session.adaptive.success(('A','short'),1,state.generation)
        self.assertEqual(len(jobs.pop(session)[1]),2)

    def test_whole_fields_merge_but_different_speakers_do_not(self):
        sources={'dialogue.A.'+str(i):SOURCE for i in range(30)}
        sources['dialogue.B.1']=SOURCE;sources['中文']='中文内容'
        folder=self.make_mod(sources);seen=[]
        async def handler(request):
            rows=json.loads(json.loads(request.content)['messages'][-1]['content'])['items'];seen.extend(rows.values())
            return httpx.Response(200,json=output(request))
        _,report=self.run_engine(handler)
        self.assertEqual(report['failure'],[])
        self.assertEqual(len(seen),2)
        self.assertEqual(report['plan']['pending_fields'],2)
        self.assertEqual(report['plan']['duplicate_fragments'],58)
        self.assertEqual(report['plan']['preserved_chinese_fields'],1)
        translated=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))
        self.assertEqual(translated['dialogue.A.0'],translated['dialogue.A.29'])
        self.assertNotIn('MLJOIN',str(seen))

    def test_memory_survives_new_context_and_preserves_existing_edits(self):
        folder=self.make_mod({'dialogue.A.1':SOURCE})
        async def handler(request):return httpx.Response(200,json=output(request))
        self.run_engine(handler)
        (folder/'i18n/default.json').write_text(json.dumps({'dialogue.A.1':SOURCE,'dialogue.A.2':SOURCE}),encoding='utf-8')
        async def unexpected(request):raise AssertionError('memory must not call API')
        _,report=self.run_engine(unexpected)
        self.assertEqual(report['metrics']['requests'],0)
        self.assertEqual(report['plan']['memory_hits'],2)

    def test_conflicting_player_edits_disable_memory(self):
        source='Hello farmer, welcome to this beautiful valley.'
        folder=self.make_mod({'dialogue.A.1':source,'dialogue.A.2':source,'dialogue.A.3':source},
                             {'dialogue.A.1':'玩家的第一份译文。','dialogue.A.2':'玩家的第二份译文。'})
        calls=[]
        async def handler(request):calls.append(request);return httpx.Response(200,json=output(request))
        _,report=self.run_engine(handler)
        self.assertEqual(report['plan']['memory_hits'],0);self.assertEqual(len(calls),1)
        targets=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))
        self.assertEqual(targets['dialogue.A.1'],'玩家的第一份译文。')
        self.assertEqual(targets['dialogue.A.2'],'玩家的第二份译文。')

    def test_plan_decline_sends_zero_requests(self):
        self.make_mod({'text':'Hello'});decision=queue.Queue();decision.put(False)
        async def unexpected(request):raise AssertionError('must not call API')
        _,report=self.run_engine(unexpected,plan_decision=decision)
        self.assertEqual(report['metrics']['requests'],0)
        self.assertIn('paused_reason',report)

    def test_short_labels_are_not_merged(self):
        self.make_mod({'label.A':'Hello','label.B':'Hello'})
        async def handler(request):return httpx.Response(200,json=output(request))
        _,report=self.run_engine(handler)
        self.assertEqual(report['plan']['pending_fields'],2)
        self.assertEqual(report['plan']['duplicate_fragments'],0)

    def test_only_failed_fragment_is_retried_and_paid_success_is_kept(self):
        self.make_mod({'dialogue.A':'$q 1/2 branch#Hello#Welcome'})
        seen=[]
        async def handler(request):
            rows=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
            seen.append([row['text'] for row in rows.values()])
            data=output(request)
            if len(seen)==1:
                data['choices'][0]['message']['content']=data['choices'][0]['message']['content'].replace('\\u6b22\\u8fce','Welcome')
            return httpx.Response(200,json=data)
        _,report=self.run_engine(handler)
        self.assertEqual(report['failure'],[])
        self.assertEqual(len(seen),2)
        self.assertNotIn('Hello',str(seen[1]));self.assertIn('Welcome',str(seen[1]))

    def test_mail_attachment_stays_outside_model_text(self):
        source='Hello farmer.^Welcome %item money 2000 5001 %%'
        folder=self.make_mod({'mail.A':source});seen=[]
        async def handler(request):
            seen.append(request.content.decode());return httpx.Response(200,json=output(request))
        _,report=self.run_engine(handler)
        self.assertEqual(report['failure'],[])
        self.assertNotIn('money',str(seen));self.assertNotIn('5001',str(seen))
        target=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))['mail.A']
        self.assertIn('%item money 2000 5001 %%',target)

    def test_deepseek_preset_and_reasoning_usage(self):
        config=preset_config('deepseek','deepseek-flash','fake-test-key')
        self.assertTrue(config.disable_thinking)
        translator=Translator(config)
        _,body,_=translator.request_parts([])
        self.assertEqual(body['thinking'],{'type':'disabled'})
        translator.parse_response({'choices':[{'message':{'content':'{}'}}],
            'usage':{'completion_tokens':20,'completion_tokens_details':{'reasoning_tokens':12}}})
        self.assertEqual(translator.last_usage['reasoning_tokens'],12)

    def test_unexpected_worker_failure_records_report_and_checkpoints(self):
        self.make_mod({'text':'Hello'})
        async def handler(request):raise RuntimeError('synthetic failure')
        _,report=self.run_engine(handler)
        self.assertIn('RuntimeError',report['paused_reason'])
        cache=Cache(self.root/'cache');self.addCleanup(cache.close)
        states=cache.db.execute('SELECT state FROM attempts').fetchall()
        self.assertEqual(states,[('pending_retry',)])
