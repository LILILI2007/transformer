import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import httpx
from modlingo.async_provider import Session, Budget, translate_recover, ServiceError
from modlingo.cache import Cache
from modlingo.core import Project, Entry
from modlingo.discovery import scan_mods
from modlingo.engine import Engine
from modlingo.provider import APIConfig
from modlingo.syntax import spans, rebuild, validate_rebuilt

def envelope(request):
    data=json.loads(request.content)
    items=json.loads(data['messages'][-1]['content'])['items']
    text=json.dumps({key:'中文'+row['text'] for key,row in items.items()},ensure_ascii=False)
    return {'choices':[{'finish_reason':'stop','message':{'content':text}}]}

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mods=self.root/'Mods';self.mods.mkdir()
        self.config=APIConfig('http://127.0.0.1:3333/v1','contract')

    def mod(self,name,count=40):
        path=self.mods/name;(path/'i18n').mkdir(parents=True)
        (path/'manifest.json').write_text(json.dumps({'UniqueID':'Test.'+name,'Name':name,'Version':'1'}),encoding='utf-8')
        (path/'i18n/default.json').write_text(json.dumps({str(i):'Hello '+str(i) for i in range(count)}),encoding='utf-8')
        return path

    def run_engine(self,handler,stop=None,concurrency=4):
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            # Match scan_worker: the companion itself isn't a selected mod.
            items=[item for item in scan_mods(self.mods) if item.mod_id!='ModLingo.Bridge']
            engine=Engine(items,self.mods,self.config,stop or threading.Event(),storage=self.root/'cache',
                          concurrency=concurrency,transport=httpx.MockTransport(handler),use_processes=False)
            return asyncio.run(engine.run())

    def test_world_map_pipeline_installs_id_paths_and_reordered_list_reuses_cache(self):
        folder=self.mods/'MapPipeline';folder.mkdir()
        (folder/'manifest.json').write_text(json.dumps({'UniqueID':'Test.MapPipeline','Name':'Map pipeline','Version':'1','ContentPackFor':{'UniqueID':'Pathoschild.ContentPatcher'}}),encoding='utf-8')
        areas=[{'Id':'first','ScrollText':'First area','Tooltips':[{'Id':'tip','Text':'First tooltip'}]},
               {'Id':'second','ScrollText':'Second area','Tooltips':[]}]
        content={'Changes':[{'Action':'EditData','Target':'Data/WorldMap','Entries':{'Region':{'MapAreas':areas}}}]}
        path=folder/'content.json';path.write_text(json.dumps(content),encoding='utf-8')
        original=path.read_bytes();calls=[]
        async def handler(request):
            calls.append(request)
            return httpx.Response(200,json=envelope(request))
        _,report=self.run_engine(handler)
        self.assertEqual(len(report['success']),1)
        packs=list((self.mods/'ModLingo.Bridge'/'translations').glob('*.json'))
        self.assertEqual(len(packs),1)
        records=json.loads(packs[0].read_text(encoding='utf-8'))['records']
        self.assertEqual({tuple(r['path']):r['target'] for r in records},{
            ('Region','MapAreas','first','ScrollText'):'中文First area',
            ('Region','MapAreas','first','Tooltips','tip','Text'):'中文First tooltip',
            ('Region','MapAreas','second','ScrollText'):'中文Second area'})
        self.assertEqual(path.read_bytes(),original)
        areas.reverse();path.write_text(json.dumps(content),encoding='utf-8')
        before=len(calls);_,second=self.run_engine(handler)
        self.assertEqual(len(calls),before)
        self.assertEqual(len(second['success']),1)
        self.assertEqual(second['metrics']['cache_hits'],3)

    def test_gmcm_runtime_labels_and_raw_choice_display_round_trip(self):
        self.assert_runtime_gmcm_pipeline(False)

    def test_empty_i18n_does_not_block_runtime_gmcm_dictionary(self):
        self.assert_runtime_gmcm_pipeline(True)

    def assert_runtime_gmcm_pipeline(self,empty_i18n):
        from modlingo.extraction import source_hash
        folder=self.mod('RuntimeGMCM',0)
        if not empty_i18n:
            (folder/'i18n/default.json').unlink();(folder/'i18n').rmdir()
        config=folder/'config.json';config.write_text('{"Destination":"jibb.Farm","DebugMode_ForceNewQuestLetter":false}')
        before=config.read_bytes()
        bridge=self.mods/'ModLingo.Bridge';bridge.mkdir()
        texts=[('gmcm/runtime/SimpleModOption/name/DebugMode_ForceNewQuestLetter','DebugMode_ForceNewQuestLetter'),
               ('gmcm/runtime/SectionTitleModOption/name','Mod Section Toggles'),
               ('gmcm/AddTextOption/formatAllowedValue/destination','jibb.Farm')]
        records=[dict(mod_id='Test.RuntimeGMCM',mod_version='1',adapter='gmcm',context=context,source=source,source_hash=source_hash(source),asset='',path=[]) for context,source in texts]
        (bridge/'captures.json').write_text(json.dumps({'schema_version':1,'captures':records+records}),encoding='utf-8')
        calls=[]
        async def handler(request):
            calls.append(request);return httpx.Response(200,json=envelope(request))
        _,report=self.run_engine(handler)
        self.assertEqual(len(report['success']),1,report)
        packs=list((bridge/'translations').glob('*.json'))
        installed=json.loads(packs[0].read_text(encoding='utf-8'))['records']
        self.assertEqual({(r['context'],r['source'],r['target']) for r in installed},
                         {(context,source,'中文'+source) for context,source in texts})
        self.assertEqual(config.read_bytes(),before)
        first=len(calls);_,second=self.run_engine(handler)
        self.assertEqual(len(calls),first)
        self.assertEqual(second['metrics']['cache_hits'],3)

    def test_quick_question_keeps_existing_branch_translation_and_fills_header(self):
        folder=self.mod('Question',0)
        source='pause 1/quickQuestion Help?#Yes#No(break)message "Thanks!"(break)pause 1/end'
        old=source.replace('Thanks!','玩家已翻译的谢谢')
        (folder/'i18n/default.json').write_text(json.dumps({'question':source}),encoding='utf-8')
        (folder/'i18n/zh.json').write_text(json.dumps({'question':old},ensure_ascii=False),encoding='utf-8')
        texts=[]
        async def handler(request):
            items=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
            texts.extend(row['text'] for row in items.values())
            return httpx.Response(200,json=envelope(request))
        _,report=self.run_engine(handler)
        target=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))['question']
        self.assertIn('玩家已翻译的谢谢',target)
        self.assertTrue(validate_rebuilt(source,target,'event'))
        self.assertFalse(any('Thanks!' in text or '玩家已翻译的谢谢' in text for text in texts))
        self.assertEqual(report['metrics']['cache_hits'],1)
        calls=len(texts);self.run_engine(handler);self.assertEqual(len(texts),calls)

    def test_old_bubble_target_keeps_player_dialogue_and_fills_new_span(self):
        folder=self.mod('Bubble',0)
        source='pause 1/textAboveHead Abigail "Hello bubble!"/message "Existing dialogue"/end'
        old=source.replace('Existing dialogue','玩家的原有对白')
        (folder/'i18n/default.json').write_text(json.dumps({'event':source}),encoding='utf-8')
        (folder/'i18n/zh.json').write_text(json.dumps({'event':old},ensure_ascii=False),encoding='utf-8')
        calls=[]
        async def handler(request):
            calls.append(request)
            return httpx.Response(200,json=envelope(request))
        _,report=self.run_engine(handler)
        target=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))['event']
        self.assertIn('玩家的原有对白',target)
        self.assertIn('中文Hello bubble!',target)
        self.assertIn('textAboveHead Abigail',target)
        self.assertEqual(len(report['success']),1)
        previous=len(calls);self.run_engine(handler);self.assertEqual(len(calls),previous)

    def test_new_bubble_span_reuses_prior_paid_fragment_cache(self):
        folder=self.mod('BubbleCache',0)
        source='pause 1/textAboveHead Abigail "Hello bubble!"/message "Existing dialogue"/end'
        (folder/'i18n/default.json').write_text(json.dumps({'event':source}),encoding='utf-8')
        cache=Cache(self.root/'cache')
        old_key=cache.key(self.config,'Test.BubbleCache','event/span/0','Existing dialogue',{})
        cache.put_many('previous',[(old_key,'Existing dialogue','原有付费缓存对白')]);cache.close()
        texts=[]
        async def handler(request):
            items=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
            texts.extend(row['text'] for row in items.values())
            return httpx.Response(200,json=envelope(request))
        _,report=self.run_engine(handler)
        target=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))['event']
        self.assertIn('原有付费缓存对白',target)
        self.assertEqual(report['metrics']['cache_hits'],1)
        self.assertFalse(any('Existing dialogue' in text for text in texts))

    def test_closing_dialogue_preserves_player_translation_and_only_fills_missing_text(self):
        folder=self.mod('Closing',0)
        source='pause 1/message "Existing dialogue"/end dialogue Abigail "Tomorrow!"'
        (folder/'i18n/default.json').write_text(json.dumps({'event':source}),encoding='utf-8')
        (folder/'i18n/zh.json').write_text(json.dumps({'event':source.replace('Existing dialogue','玩家原有对白')},ensure_ascii=False),encoding='utf-8')
        calls=[]
        async def handler(request):
            calls.append(request)
            return httpx.Response(200,json=envelope(request))
        _,report=self.run_engine(handler)
        target=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))['event']
        self.assertIn('玩家原有对白',target)
        self.assertIn('中文Tomorrow!',target)
        self.assertEqual(len(report['success']),1)
        before=len(calls);self.run_engine(handler);self.assertEqual(len(calls),before)

    def test_closing_in_branch_does_not_shift_paid_bubble_or_message_contexts(self):
        folder=self.mod('ClosingCache',0)
        source='quickQuestion Help?#Yes(break)end dialogue Abigail "Mine"/textAboveHead Abigail "Mine"/message "Mine"'
        (folder/'i18n/default.json').write_text(json.dumps({'event':source}),encoding='utf-8')
        cache=Cache(self.root/'cache')
        for context,original,target in [('span/0','Help?','帮助？'),('span/1','Yes','好的'),('bubble/2','Mine','头顶气泡'),('span/3','Mine','矿井')]:
            key=cache.key(self.config,'Test.ClosingCache','event/'+context,original,{})
            cache.put_many('previous',[(key,original,target)])
        cache.close();calls=[]
        async def handler(request):
            calls.append(request)
            return httpx.Response(200,json=envelope(request))
        _,report=self.run_engine(handler)
        target=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))['event']
        parts,_=spans(target,'event')
        self.assertEqual([p.text for p in parts],['帮助？','好的','中文Mine','头顶气泡','矿井'])
        self.assertEqual(report['metrics']['cache_hits'],4)
        self.assertEqual(len(calls),1)

    def test_bubble_cache_does_not_alias_old_message_with_identical_source(self):
        folder=self.mod('BubbleIdentity',0)
        source='pause 1/textAboveHead Abigail "Mine"/message "Mine"/end'
        (folder/'i18n/default.json').write_text(json.dumps({'event':source}),encoding='utf-8')
        cache=Cache(self.root/'cache')
        old_key=cache.key(self.config,'Test.BubbleIdentity','event/span/0','Mine',{})
        cache.put_many('previous',[(old_key,'Mine','矿井')]);cache.close()
        calls=[]
        async def handler(request):
            calls.append(request)
            return httpx.Response(200,json=envelope(request))
        self.run_engine(handler)
        target=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))['event']
        parts,notes=spans(target,'event')
        self.assertEqual([p.text for p in parts],['中文Mine','矿井'])
        self.assertEqual(len(calls),1)

    def test_concurrent_results_install_and_second_run_has_zero_requests(self):
        path=self.mod('A',100)
        active=peak=calls=0
        async def handler(request):
            nonlocal active,peak,calls
            calls+=1;active+=1;peak=max(peak,active)
            await asyncio.sleep(.03)
            active-=1
            return httpx.Response(200,json=envelope(request))
        _,report=self.run_engine(handler)
        self.assertEqual(peak,4)
        self.assertEqual(len(report['success']),1)
        self.assertEqual(len(json.loads((path/'i18n/zh.json').read_text(encoding='utf-8'))),100)
        previous=calls
        _,report=self.run_engine(handler)
        self.assertEqual(calls,previous)
        self.assertEqual(report['metrics']['requests'],0)

    def test_stop_cancels_waiting_requests_without_dispatching_more(self):
        self.mod('A',200)
        stop=threading.Event();calls=0;stopped_at=[];cancelled_at=[]
        async def handler(request):
            nonlocal calls
            calls+=1
            if not stopped_at:stopped_at.append(time.monotonic())
            stop.set()
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled_at.append(time.monotonic())
                raise
            return httpx.Response(200,json=envelope(request))
        _,report=self.run_engine(handler,stop)
        self.assertTrue(stopped_at)
        self.assertEqual(len(cancelled_at),calls)
        self.assertLess(max(cancelled_at)-stopped_at[0],1)
        self.assertLessEqual(calls,4)
        self.assertTrue(any(r['status']=='已暂停' for r in report['failure']))

    def test_joined_marker_failure_recovers_smaller_fields_with_shared_budget(self):
        import re
        folder=self.mod('SplitField',0)
        source='$q 1/2 next#Hello @. Help on %farm?#$r 1 0 yes#Yes please#$r 2 0 no#Not yet'
        (folder/'i18n/default.json').write_text(json.dumps({'question':source}),encoding='utf-8')
        calls=[]
        async def handler(request):
            items=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
            calls.append([value['text'] for value in items.values()])
            output={key:re.sub(r'__ML_[A-Za-z0-9_]+?__','','中文'+value['text'],count=1) for key,value in items.items()}
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output)}}]})
        _,report=self.run_engine(handler)
        self.assertEqual(len(report['success']),1)
        self.assertGreater(len(calls),1)
        self.assertLessEqual(len(calls),8)
        target=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))['question']
        self.assertTrue(validate_rebuilt(source,target))
        trace=next((self.root/'cache/reports').glob('*.requests.jsonl'))
        rows=[json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any(row['event']=='field_split' for row in rows))
        self.assertEqual(len({row['batch'] for row in rows if row['event']=='http'}),1)
        self.assertTrue(any(row.get('marker_diagnostics') for row in rows))

    def test_field_splitting_cannot_reset_eight_request_limit(self):
        folder=self.mod('SplitLimit',0)
        (folder/'i18n/default.json').write_text(json.dumps({'large':'#'.join('Hello '+str(i) for i in range(100))}),encoding='utf-8')
        calls=[]
        async def handler(request):
            calls.append(request)
            return httpx.Response(200,json={'choices':[{'message':{'content':'invalid JSON'}}]})
        _,report=self.run_engine(handler)
        self.assertLessEqual(len(calls),8)
        self.assertEqual(len(report['success']),0)
        self.assertFalse((folder/'i18n/zh.json').exists())

    def test_partial_ids_are_retained_and_only_missing_item_retried(self):
        async def run():
            seen=[];results=[];errors=[]
            async def handler(request):
                data=json.loads(request.content);items=json.loads(data['messages'][-1]['content'])['items']
                seen.append(len(items))
                output={k:'中文'+v['text'] for k,v in items.items() if len(seen)>1 or k!='1'}
                return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output)}}]})
            async with Session(self.config,transport=httpx.MockTransport(handler)) as session:
                await translate_recover(session,[('a','Hello'),('b','Welcome')],{},lambda rows,usage:results.extend(rows),lambda rows,error:errors.append(error))
            self.assertEqual(seen,[2,1]);self.assertEqual(len(results),2);self.assertEqual(errors,[])
        asyncio.run(run())

    def test_spawned_preparation_process_with_real_ipc(self):
        self.mod('A',3)
        async def handler(request):
            return httpx.Response(200,json=envelope(request))
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            engine=Engine(scan_mods(self.mods),self.mods,self.config,threading.Event(),storage=self.root/'cache',transport=httpx.MockTransport(handler))
            _,report=asyncio.run(engine.run())
        self.assertEqual(len(report['success']),1)

    def test_total_format_retry_budget_is_eight(self):
        async def run():
            calls=0
            async def handler(request):
                nonlocal calls
                calls+=1
                return httpx.Response(200,json={'choices':[{'message':{'content':'not JSON'}}]})
            errors=[]
            async with Session(self.config,transport=httpx.MockTransport(handler)) as session:
                await translate_recover(session,[(str(i),'Hello') for i in range(20)],{},lambda rows,usage:None,lambda rows,error:errors.extend(rows))
            self.assertLessEqual(calls,8);self.assertEqual(calls,3);self.assertTrue(errors)
        asyncio.run(run())

    def test_no_credentials_follow_redirect_and_auth_is_fatal(self):
        async def run():
            for status in (302,401):
                calls=[]
                async def handler(request):
                    calls.append(str(request.url))
                    return httpx.Response(status,headers={'Location':'https://other.example'})
                async with Session(self.config,transport=httpx.MockTransport(handler)) as session:
                    from modlingo.provider import Translator
                    with self.assertRaises(ValueError):
                        await session.post(Translator(self.config),[],Budget())
                self.assertEqual(len(calls),1)
        asyncio.run(run())

    def test_cache_migration_is_idempotent_and_keeps_source(self):
        file=self.root/'old.json'
        Project('Test.A','A','A',entries={'a':Entry('Hello','你好','ai','Hello')}).save(file)
        cache=Cache(self.root/'cache');self.addCleanup(cache.close)
        self.assertEqual(cache.migrate(file),1)
        self.assertEqual(cache.migrate(file),0)
        self.assertTrue(file.exists());self.assertEqual(cache.legacy('Test.A','a','Hello'),'你好')
        other=APIConfig('http://127.0.0.1:3333/v1','different')
        self.assertNotEqual(cache.key(self.config,'A','k','Hello',{}),cache.key(other,'A','k','Hello',{}))

    def test_scripts_rebuild_only_human_spans(self):
        for source,kind in [('pause 100/speak Abigail "Hello / farmer"/end','event'),('${Sir^Madam}$, welcome!','text'),('$q 1/2 branch#Hello#$r 1 0 yes#Welcome','text'),('ItemDelivery/Title/Description/Objective/Robin 788/-1/250/-1','quest')]:
            parts,notes=spans(source,kind)
            self.assertTrue(parts,(source,notes))
            target=rebuild(source,parts,{str(i):'中文'+p.text for i,p in enumerate(parts)})
            self.assertTrue(validate_rebuilt(source,target,kind))
            self.assertFalse(validate_rebuilt(source,target+'$q 99',kind))

    def test_progress_heartbeat_continues_during_slow_response(self):
        self.mod('A',1);events=[]
        async def handler(request):
            await asyncio.sleep(2.15)
            return httpx.Response(200,json=envelope(request))
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            engine=Engine(scan_mods(self.mods),self.mods,self.config,threading.Event(),self.root/'cache',
                notify=lambda kind,data:events.append((time.monotonic(),data.copy())) if kind=='engine_progress' else None,
                transport=httpx.MockTransport(handler),use_processes=False)
            asyncio.run(engine.run())
        pending=[at for at,data in events if data['in_flight']==1 and data['completed']==0]
        self.assertGreaterEqual(len(pending),2)
        self.assertLess(pending[1]-pending[0],1.3)

    def test_mod_batches_are_scheduled_round_robin(self):
        self.mod('A',100);self.mod('B',1);seen=[]
        async def handler(request):
            items=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
            seen.append([row['context_key'] for row in items.values()])
            return httpx.Response(200,json=envelope(request))
        self.run_engine(handler,concurrency=1)
        self.assertTrue(any('Test.A' in key for key in seen[0]))
        self.assertTrue(any('Test.B' in key for key in seen[1]))
