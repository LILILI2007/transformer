import asyncio
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from modlingo.cache import Cache
from modlingo.discovery import scan_mods, ModItem
from modlingo.engine import Engine
from modlingo.provider import APIConfig
from modlingo.failures import Circuits, Reason
from modlingo.batch import validate_item, installation_conflicts


class FailureIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mods=self.root/'Mods';self.mods.mkdir()
        self.config=APIConfig('http://127.0.0.1:3333/v1','fixture')

    def mod(self,name,count):
        path=self.mods/name;(path/'i18n').mkdir(parents=True)
        (path/'manifest.json').write_text(json.dumps(dict(UniqueID=name,Name=name,Version='1')),encoding='utf-8')
        (path/'i18n/default.json').write_text(json.dumps({str(i):'Hello '+str(i) for i in range(count)}),encoding='utf-8')
        return path

    def run_engine(self,handler,**kwargs):
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            engine=Engine(scan_mods(self.mods),self.mods,self.config,threading.Event(),self.root/'cache',
                          concurrency=1,use_processes=False,transport=httpx.MockTransport(handler),**kwargs)
            return asyncio.run(engine.run())[1]

    def test_unchanged_does_not_stop_other_mod_and_persists(self):
        self.mod('Difficult',2);self.mod('Healthy',2)
        calls=[]
        async def handler(request):
            data=json.loads(request.content);items=json.loads(data['messages'][-1]['content'])['items'];calls.append(data)
            values={k:v['text'] if 'Difficult' in v['context_key'] else '你好 '+v['text'] for k,v in items.items()}
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(values)}}]})
        first=self.run_engine(handler)
        self.assertNotIn('paused_reason',first)
        self.assertEqual([r['mod_id'] for r in first['success']],['Healthy'])
        self.assertTrue(all(d['code']=='unchanged' and d['attempts']==2 and d['exhausted'] for d in first['failure'][0]['diagnostics']))
        count=len(calls);self.run_engine(handler);self.assertEqual(len(calls),count)
        self.run_engine(handler,retry_exhausted=True);self.assertGreater(len(calls),count)
        self.assertTrue(any('上次输出' in row['messages'][0]['content'] for row in calls))

    def test_offline_mode_cannot_dispatch(self):
        self.mod('Offline',2)
        async def handler(request):raise AssertionError('network forbidden')
        report=self.run_engine(handler,offline_only=True)
        self.assertEqual(report['metrics']['requests'],0)
        self.assertFalse((self.mods/'Offline/i18n/zh.json').exists())

    def test_pending_records_do_not_count_duplicate_install_diagnostics(self):
        path=self.mod('Partial',2)
        (path/'i18n/zh.json').write_text('{"0":"已有中文"}',encoding='utf-8')
        async def forbidden(request):raise AssertionError('network forbidden')
        report=self.run_engine(forbidden,offline_only=True)
        row=report['failure'][0]
        self.assertEqual(row['pending_records'],1)
        self.assertEqual(row['coverage']['qualified_records'],1)
        self.assertEqual(len(row['skipped']),1)
        self.assertGreater(row['diagnostic_count'],len(row['skipped']))
        self.assertEqual(len(row['skipped'][0]['reasons']),2)
        self.assertEqual(json.loads((path/'i18n/zh.json').read_text(encoding='utf-8')),{'0':'已有中文'})

    def test_empty_ids_keep_distinct_scan_causes(self):
        items=[ModItem(self.mods/'BadJSON','BadJSON',reason='JSON 格式错误：测试'),
               ModItem(self.mods/'NoAccess','NoAccess',reason='无法读取目录：测试')]
        with patch('modlingo.engine.check_game_stopped'):
            _,report=asyncio.run(Engine(items,self.mods,self.config,threading.Event(),self.root/'cache',
                                       offline_only=True,use_processes=False).run())
        self.assertEqual([r['reason'] for r in report['failure']],[item.reason for item in items])

    def test_changed_cp_resource_blocks_only_its_install(self):
        folder=self.mods/'ChangedCP';folder.mkdir()
        (folder/'manifest.json').write_text(json.dumps({'UniqueID':'ChangedCP','Name':'ChangedCP','Version':'1',
            'ContentPackFor':{'UniqueID':'Pathoschild.ContentPatcher'}}))
        source=folder/'content.json'
        source.write_text('{"ConfigSchema":{"Alpha":{"AllowValues":"true,false"}},"Changes":[]}')
        healthy=self.mod('Healthy',1)
        async def handler(request):
            rows=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
            source.write_text('{"ConfigSchema":{"Beta":{"AllowValues":"true,false"}},"Changes":[]}')
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({k:'中文'+v['text'] for k,v in rows.items()})}}]})
        report=self.run_engine(handler)
        bad=next(row for row in report['failure'] if row['mod_id']=='ChangedCP')
        self.assertIn('content.json',bad['reason'])
        self.assertEqual(bad['installed'],0)
        self.assertFalse((folder/'i18n/zh.json').exists())
        self.assertTrue((healthy/'i18n/zh.json').exists())

    def test_shared_output_reports_both_owners_only(self):
        from types import SimpleNamespace
        paths=[self.mods/'Shared',self.mods/'Shared',self.mods/'Independent']
        bundles=[SimpleNamespace(item=ModItem(p,str(i),mod_id=str(i)),
                                 project=SimpleNamespace(entries={'hello':object()})) for i,p in enumerate(paths)]
        conflicts=installation_conflicts(bundles)
        self.assertEqual(set(conflicts),{0,1})
        self.assertIn('shared',conflicts[0])

    def test_exhausted_fragment_repair_keeps_unchanged_reason(self):
        from modlingo.async_provider import Session, translate_recover
        async def run():
            failures=[];requests=[]
            async def handler(request):
                requests.append(request)
                return httpx.Response(200,json={'choices':[{'message':{'content':'{"0":"你好"}'}}]})
            async with Session(self.config,transport=httpx.MockTransport(handler)) as session:
                await translate_recover(session,[('a','Hello')],{},
                    lambda rows,usage:[('repair','Hello')],lambda rows,error:failures.append(error),
                    repair_reason=lambda item:Reason('片段原样返回','unchanged'))
            self.assertEqual(len(requests),2)
            self.assertEqual([e.code for e in failures],['unchanged'])
        asyncio.run(run())

    def test_circuits_require_three_mods_for_global_pause(self):
        circuit=Circuits()
        for _ in range(4):self.assertFalse(circuit.finish(('A','gmcm','text'),0,['structure']))
        self.assertIn(('A','gmcm','text'),circuit.paused)
        self.assertFalse(circuit.finish(('B','gmcm','text'),0,['structure']))
        self.assertTrue(circuit.finish(('C','gmcm','text'),0,['structure']))
        circuit=Circuits()
        for i in range(10):self.assertFalse(circuit.finish((str(i),'gmcm','text'),0,['unchanged']))
        circuit=Circuits()
        for i in range(5):self.assertFalse(circuit.finish((str(i),'gmcm','text'),0,['response_format']))
        self.assertTrue(circuit.finish(('sixth','gmcm','text'),0,['response_format']))

    def test_local_service_configuration_failure_does_not_retry(self):
        from modlingo.async_provider import Session, translate_recover, ServiceError
        async def run():
            async def forbidden(request):raise AssertionError('no request with missing credentials')
            config=APIConfig('https://example.invalid/v1','fixture')
            async with Session(config,transport=httpx.MockTransport(forbidden)) as session:
                with self.assertRaises(ServiceError) as caught:
                    await translate_recover(session,[('a','Hello')],{},lambda *args:None,lambda *args:None)
                self.assertEqual(caught.exception.code,'service_config')
                self.assertEqual(session.requests,0)
        asyncio.run(run())

    def test_local_pause_leaves_other_mod_running_and_untried_items_unexhausted(self):
        self.mod('Difficult',120);self.mod('Healthy',60)
        async def handler(request):
            rows=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
            values={k:v['text'] if 'Difficult' in v['context_key'] else '中文'+v['text'] for k,v in rows.items()}
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(values)}}]})
        report=self.run_engine(handler)
        self.assertNotIn('paused_reason',report)
        self.assertEqual([r['mod_id'] for r in report['success']],['Healthy'])
        self.assertIn(['Difficult','i18n','text'],report['scope_pauses'])
        diagnostics=report['failure'][0]['diagnostics']
        waiting=[d for d in diagnostics if d['code']=='scope_paused']
        self.assertTrue(waiting)
        self.assertTrue(all(not d['exhausted'] and d['attempts']==0 for d in waiting))
        self.assertTrue(all(d['attempts']==2 for d in diagnostics if d['code']=='unchanged'))

    def test_cross_mod_protocol_failures_trigger_global_pause(self):
        for name in ('A','B','C'):self.mod(name,120)
        async def handler(request):
            return httpx.Response(200,json={'choices':[{'message':{'content':'not JSON'}}]})
        report=self.run_engine(handler)
        self.assertIn('响应协议或结构异常',report['paused_reason'])
        self.assertEqual(report['scope_pauses'],[])

    def test_diagnostics_survive_pending_and_migration(self):
        db=Cache(self.root/'cache')
        db.record_failure(['key'],Reason('结构失败','structure'),2,True)
        db.mark('run','key','pending_retry','全局暂停')
        db.close();db=Cache(self.root/'cache');self.addCleanup(db.close)
        self.assertEqual(db.diagnostic('key')['reason'],'结构失败')
        db.put_many('run',[('key','Hello','你好')]);self.assertIsNone(db.diagnostic('key'))

    def test_snapshot_names_changed_file(self):
        path=self.mod('Snapshot',2);item=scan_mods(self.mods)[0]
        (path/'i18n/default.json').write_text('{"key":"Changed"}')
        with self.assertRaisesRegex(ValueError,'i18n/default.json'):validate_item(item,self.mods)

    def test_confirmation_wait_is_separate(self):
        self.mod('Wait',1);decision=queue.Queue()
        async def handler(request):
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"0":"你好"}'}}]})
        timer=threading.Timer(.3,lambda:decision.put(True));timer.start()
        try:report=self.run_engine(handler,plan_decision=decision)
        finally:timer.join()
        self.assertGreater(report['timings']['confirmation_wait_seconds'],.15)
        self.assertLess(report['timings']['planning_seconds'],report['timings']['confirmation_wait_seconds'])
