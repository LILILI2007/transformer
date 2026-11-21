import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from modlingo.cache import Cache
from modlingo.dependencies import DependencyGraph
from modlingo.discovery import scan_mods
from modlingo.engine import Engine
from modlingo.extraction import extract
from modlingo.provider import APIConfig
from modlingo.memory import memory_key


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name);self.mods = self.root/'Mods';self.mods.mkdir()
        self.config = APIConfig('http://localhost:3333/v1', 'fixture')
        self.calls = []

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def pack(self, name, uid, source=None, changes=None, config=None):
        p = self.mods/name
        self.write(p/'manifest.json', {'UniqueID':uid,'Name':'测试模组','Version':'1',
            'ContentPackFor':{'UniqueID':'Pathoschild.ContentPatcher'}})
        self.write(p/'content.json', {'Changes':changes or [],'ConfigSchema':config or {}})
        if source is not None:self.write(p/'i18n/default.json',source)
        return p

    def consumer(self, name='AConsumer'):
        p = self.pack(name, 'jibb.minecarts', {}, config={
            '1.':{'AllowValues':'Mod.Dock','Default':'Mod.Dock'},
            '2.':{'AllowValues':'Mod.Dock','Default':'Mod.Dock'}})
        self.write(p/'config.json',{'1.':'Mod.Dock','2.':'Mod.Dock'})
        return p

    def provider(self, name='ZProvider', label='{{i18n:place}}', source=None, network='Default'):
        return self.pack(name,'Fixture.'+name,source,changes=[{'Action':'EditData','Target':'Data/Minecarts',
            'Entries':{network:{'Destinations':[{'Id':'Mod.Dock','DisplayName':label}]}}}])

    async def translated(self, request):
        items=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
        texts=[row['text'] for row in items.values()]
        self.calls.extend(texts)
        self.assertNotIn('Mod.Dock',texts)
        content={key:('码头' if row['text'] in ('The pier','Dock') else '中文'+row['text']) for key,row in items.items()}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(content,ensure_ascii=False)}}]})

    def run_engine(self, handler=None, **kwargs):
        items=[i for i in scan_mods(self.mods) if i.mod_id!='ModLingo.Bridge']
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            engine=Engine(items,self.mods,self.config,kwargs.pop('stop',None) or threading.Event(),self.root/'cache',
                transport=httpx.MockTransport(handler or self.translated),use_processes=False,**kwargs)
            _,report=asyncio.run(engine.run())
        self.assertNotIn('paused_reason',report,report.get('paused_reason'))
        return report

    def assert_backfill(self, p, report):
        language=json.loads((p/'i18n/zh.json').read_text(encoding='utf-8'))
        self.assertEqual(language['config.1..values.Mod.Dock'],'码头')
        self.assertEqual(language['config.2..values.Mod.Dock'],'码头')
        self.assertEqual(report['dependencies']['same_run_backfilled_fields'],2)
        self.assertEqual(report['dependencies']['pending_fields'],0)
        self.assertEqual(report['metrics']['waiting_dependencies'],0)
        self.assertEqual(report['metrics']['dependency_hits'],2)
        self.assertEqual(report['metrics']['failed'],0)

    def assert_pending(self, p):
        language=json.loads((p/'i18n/zh.json').read_text(encoding='utf-8')) if (p/'i18n/zh.json').exists() else {}
        for key in ('config.1..values.Mod.Dock','config.2..values.Mod.Dock'):
            self.assertIn(language.get(key),(None,'Mod.Dock'))

    def test_new_i18n_provider_backfills_consumer_in_same_run_independent_of_order(self):
        p=self.consumer();provider=self.provider(source={'place':'The pier'})
        original=(p/'config.json').read_bytes(),(provider/'content.json').read_bytes()
        report=self.run_engine()
        self.assert_backfill(p,report)
        self.assertEqual(self.calls,['The pier'])
        self.assertEqual(original,((p/'config.json').read_bytes(),(provider/'content.json').read_bytes()))
        row=next(r for r in report['success'] if r['mod_id']=='jibb.minecarts')
        self.assertTrue(all(r['same_run_dependency'] for r in row['display_label_reuse']))
        self.calls.clear();second=self.run_engine()
        self.assertEqual(self.calls,[])
        self.assertEqual(second['metrics']['requests'],0)

    def test_new_literal_asset_provider_backfills_without_translating_id(self):
        p=self.consumer('ZConsumer');self.provider('AProvider',label='Dock')
        report=self.run_engine()
        self.assert_backfill(p,report)
        self.assertEqual(self.calls,['Dock'])

    def test_cached_local_provider_backfills_without_network(self):
        p=self.consumer()
        self.write(p/'i18n/default.json',{'Mod.Dock':'The pier'})
        item=scan_mods(self.mods)[0];bundle=extract(item)
        cache=Cache(self.root/'cache')
        for unit in bundle.units:
            if bundle.records[unit.record].context=='Mod.Dock':
                cache.put(cache.key(self.config,unit.mod_id,unit.context,unit.source,{}),'码头')
        cache.close()
        self.assert_backfill(p,self.run_engine(offline_only=True))
        self.assertEqual(self.calls,[])

    def test_old_bad_identifier_cache_is_not_used_without_name_evidence(self):
        p=self.consumer();bundle=extract(scan_mods(self.mods)[0]);cache=Cache(self.root/'cache')
        for unit in bundle.units:cache.put(cache.key(self.config,unit.mod_id,unit.context,unit.source,{}),'错误猜测')
        cache.memory_put_many((key,'错误猜测') for record in bundle.records if
            (key:=memory_key(cache,self.config,bundle,record)))
        cache.close()
        report=self.run_engine(offline_only=True)
        self.assertEqual(report['dependencies']['pending_fields'],2)
        self.assert_pending(p)
        self.assertEqual(self.calls,[])

    def test_failed_provider_leaves_dependent_fields_pending_without_id_request(self):
        p=self.consumer();self.provider(source={'place':'The pier'})
        async def fail(request):
            items=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
            self.calls.extend(row['text'] for row in items.values())
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({key:row['text'] for key,row in items.items()})}}]})
        report=self.run_engine(fail)
        self.assertNotIn('Mod.Dock',self.calls)
        self.assertEqual(report['dependencies']['pending_fields'],2)
        self.assertTrue(any('提供者翻译失败' in text for text in report['dependencies']['pending_reasons']))
        self.assert_pending(p)

    def test_all_conflicting_provider_variants_must_agree(self):
        p=self.consumer();self.provider('B',source={'place':'Dock'})
        self.provider('C',label='另一个码头')
        report=self.run_engine()
        self.assertEqual(report['dependencies']['pending_fields'],2)
        self.assertTrue(any('冲突' in r for r in report['dependencies']['pending_reasons']))
        self.assert_pending(p)

    def test_changed_same_run_provider_stops_consumer_install(self):
        p=self.consumer();provider=self.provider(source={'place':'Dock'})
        def notify(kind,data):
            if kind=='engine_progress' and data.get('phase')=='保存/安装已校验结果':
                self.write(provider/'i18n/default.json',{'place':'Changed dock'})
        report=self.run_engine(notify=notify)
        row=next(r for r in report['failure'] if r['mod_id']=='jibb.minecarts')
        self.assertEqual(row['status'],'文件异常')
        self.assertFalse((p/'i18n/zh.json').exists())

    def test_graph_detects_cycles_without_marking_downstream_tasks_as_cycles(self):
        graph=DependencyGraph()
        graph.add(('consumer',),[('name',)])
        graph.add(('name',),[('provider',)])
        self.assertEqual(graph.cycles(),set())
        graph.add(('provider',),[('name',)])
        self.assertEqual(graph.cycles(),{('name',),('provider',)})
        graph.add(('name',),[('alternate',)])
        graph.add(('alternate',),[('provider',)])
        self.assertEqual(graph.cycles(),{('name',),('provider',),('alternate',)})
        for index in range(1200):graph.add(('chain',index),[('chain',index+1)])
        self.assertEqual(graph.cycles(),{('name',),('provider',),('alternate',)})

    def test_untranslated_local_variant_blocks_early_reuse_of_an_existing_name(self):
        p=self.consumer()
        self.write(p/'i18n/default.json',{'Mod.Dock':'Dock',
            'config.EnableDock.name':'Another pier','config.EnableDock.description':'Mod.Dock'})
        self.write(p/'i18n/zh.json',{'Mod.Dock':'码头'})
        from modlingo.compatibility import minecart_labels
        bundle=extract(scan_mods(self.mods)[0])
        resolved,pending=minecart_labels(bundle)
        self.assertEqual(resolved,{})
        self.assertEqual(len(pending),3)
        # The second translation conflicts; no initial provisional label sticks.
        report=self.run_engine()
        self.assertEqual(report['dependencies']['pending_fields'],3)
        self.assert_pending(p)

    def test_backfill_unblocks_while_an_unrelated_request_is_still_running(self):
        p=self.consumer();self.provider(source={'place':'Dock'})
        self.pack('Slow','Fixture.Slow',{'line':'Unrelated long dialogue'})
        signals={}
        async def handler(request):
            if not signals:
                signals.update(started=asyncio.Event(),backfilled=asyncio.Event())
            items=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
            if any(row['text']=='Unrelated long dialogue' for row in items.values()):
                signals['started'].set()
                await asyncio.wait_for(signals['backfilled'].wait(),3)
            else:
                await asyncio.wait_for(signals['started'].wait(),3)
            return await self.translated(request)
        def notify(kind,data):
            if kind=='engine_progress' and data.get('dependency_hits')==2 and not signals['backfilled'].is_set():
                self.assertEqual(data['phase'],'并发翻译')
                signals['backfilled'].set()
        self.assert_backfill(p,self.run_engine(handler,notify=notify,concurrency=4))

    def test_stop_preserves_provider_cache_and_next_run_finishes_without_api(self):
        p=self.consumer();self.provider(source={'place':'Dock'})
        stop=threading.Event()
        async def handler(request):
            response=await self.translated(request)
            stop.set()
            return response
        # A user stop is an expected pause, unlike an unexpected engine failure.
        items=scan_mods(self.mods)
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            _,first=asyncio.run(Engine(items,self.mods,self.config,stop,self.root/'cache',
                transport=httpx.MockTransport(handler),use_processes=False).run())
        self.assertIn('paused_reason',first)
        self.assertFalse((p/'i18n/zh.json').exists())
        self.calls.clear()
        second=self.run_engine(offline_only=True)
        language=json.loads((p/'i18n/zh.json').read_text(encoding='utf-8'))
        self.assertEqual(language['config.1..values.Mod.Dock'],'码头')
        self.assertEqual(second['metrics']['requests'],0)
        self.assertEqual(self.calls,[])

    def test_multi_level_name_dependencies_drain_to_completion_in_one_run(self):
        p=self.consumer()
        self.write(p/'content.json',{'Changes':[],'ConfigSchema':{
            '1.':{'AllowValues':'Mod.Dock,Other.ID,Third.ID','Default':'Mod.Dock'}}})
        self.write(p/'i18n/default.json',{'Mod.Dock':'Other.ID','Other.ID':'Third.ID','Third.ID':'The pier'})
        report=self.run_engine()
        language=json.loads((p/'i18n/zh.json').read_text(encoding='utf-8'))
        for raw in ('Mod.Dock','Other.ID','Third.ID'):
            self.assertEqual(language['config.1..values.'+raw],'码头')
        self.assertEqual(language['Mod.Dock'],'码头')
        self.assertEqual(language['Other.ID'],'码头')
        self.assertEqual(self.calls,['The pier'])
        self.assertEqual(report['dependencies']['same_run_backfilled_fields'],5)
        self.assertEqual(report['dependencies']['pending_fields'],0)
        self.assertEqual(report['metrics']['failed'],0)

    def test_self_referencing_name_is_a_cycle_and_never_reaches_api(self):
        p=self.consumer();self.write(p/'i18n/default.json',{'Mod.Dock':'Mod.Dock'})
        report=self.run_engine()
        self.assertEqual(self.calls,[])
        self.assertEqual(report['dependencies']['pending_fields'],3)
        self.assertTrue(any('循环' in text for text in report['dependencies']['pending_reasons']))
        self.assert_pending(p)

    def test_typed_name_still_reuses_when_legacy_mod_title_has_no_chinese(self):
        p=self.consumer()
        self.write(p/'i18n/default.json',{'config.EnableDock.name':'A long mod title',
            'config.EnableDock.description':'Mod.Dock'})
        self.write(p/'i18n/zh.json',{'config.EnableDock.name':'Mod.Dock'})
        self.provider(label='码头')
        report=self.run_engine(offline_only=True)
        language=json.loads((p/'i18n/zh.json').read_text(encoding='utf-8'))
        self.assertEqual(language['config.1..values.Mod.Dock'],'码头')
        self.assertEqual(language['config.2..values.Mod.Dock'],'码头')
        self.assertEqual(report['dependencies']['pending_fields'],0)
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
