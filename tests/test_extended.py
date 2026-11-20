import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import httpx
from modlingo.async_provider import Session, Budget
from modlingo.bridge_io import install_dictionary, restore_dictionary, captures
from modlingo.cache import Cache
from modlingo.core import Entry, Project
from modlingo.discovery import scan_mods
from modlingo.engine import Engine
from modlingo.extraction import extract, source_hash
from modlingo.provider import APIConfig, Translator
from test_engine import envelope


class ExtendedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mods=self.root/'Mods';self.mods.mkdir()
        self.folder=self.mods/'Fixture';self.folder.mkdir()
        self.manifest={'UniqueID':'Test.Fixture','Name':'Fixture','Version':'1'}
        self.config=APIConfig('http://127.0.0.1:3333/v1','contract')

    def write(self, path, data):
        path=self.folder/path;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data),encoding='utf-8')

    def item(self):
        self.write('manifest.json',self.manifest)
        return scan_mods(self.mods)[0]

    def test_target_field_prefix_is_preserved_in_entries_and_fields(self):
        self.manifest['ContentPackFor']={'UniqueID':'Pathoschild.ContentPatcher'}
        self.write('content.json',{'Changes':[
            {'Action':'EditData','Target':'Data/Objects','TargetField':['TestObject'],'Entries':{'DisplayName':'Garden flower','Price':200,'Name':'internal_id'}},
            {'Action':'EditData','Target':'Data/Characters','TargetField':['TestNPC','CustomFields',0],'Fields':{'TestEntry':{'Description':'A friendly character'}}}]})
        records=extract(self.item()).records
        self.assertEqual([r.path for r in records],[['TestObject','DisplayName'],['TestNPC','CustomFields','0','TestEntry','Description']])
        self.assertEqual(records[0].context,'Data/Objects/TestObject/DisplayName')

    def test_dynamic_and_invalid_target_fields_do_not_create_wrong_paths(self):
        self.manifest['ContentPackFor']={'UniqueID':'Pathoschild.ContentPatcher'}
        for scope in ('TestObject',['{{SelectedObject}}'],[True],[{}],['a']*13):
            self.write('content.json',{'Changes':[{'Action':'EditData','Target':'Data/Objects','TargetField':scope,'Entries':{'DisplayName':'Garden flower'}}]})
            bundle=extract(self.item())
            self.assertEqual(bundle.records,[])
            self.assertEqual(bundle.category,'需进游戏采集')
            self.assertTrue(any('TargetField' in note for note in bundle.notes))

    def test_image_only_load_is_not_unknown_text(self):
        self.manifest['ContentPackFor']={'UniqueID':'Pathoschild.ContentPatcher'}
        self.write('content.json',{'Changes':[{'Action':'Load','Target':'Portraits/Test','FromFile':'assets/test.png'}]})
        self.assertEqual(extract(self.item()).category,'无需翻译')

    def test_image_pack_configuration_uses_native_labels(self):
        self.manifest['ContentPackFor']={'UniqueID':'Pathoschild.ContentPatcher'}
        self.write('content.json',{'ConfigSchema':{'Style':{'AllowValues':'A,B'}},'Changes':[{'Action':'Load','Target':'Portraits/Test','FromFile':'assets/test.png'}]})
        bundle=extract(self.item())
        self.assertEqual(bundle.category,'可直接翻译')
        self.assertIn('config.Style.name',[r.context for r in bundle.records])

    def test_rate_limit_blocks_waiters_and_reduces_concurrency(self):
        async def run():
            times=[]
            async def handler(request):
                times.append(time.monotonic())
                return httpx.Response(429,headers={'Retry-After':'0.5'}) if len(times)==1 else httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
            async with Session(self.config,4,transport=httpx.MockTransport(handler)) as session:
                translator=Translator(self.config)
                await asyncio.gather(*(session.post(translator,[],Budget()) for _ in range(12)))
                self.assertEqual(session.limit,2)
            # Up to four requests may already be on the wire when 429 arrives.
            self.assertGreaterEqual(min(times[4:])-times[0],.48)
        asyncio.run(run())

    def test_total_timeout_is_bounded_including_body(self):
        async def run():
            async def handler(request):
                await asyncio.sleep(5)
            config=APIConfig('http://127.0.0.1:3333/v1','contract',timeout=1)
            started=time.monotonic()
            async with Session(config,transport=httpx.MockTransport(handler)) as session:
                with self.assertRaises(ValueError):
                    await session.post(Translator(config),[],Budget())
                self.assertEqual(session.requests,3)
            self.assertLess(time.monotonic()-started,5)
        asyncio.run(run())

    def test_includes_load_visible_fields_and_corrupt_reference(self):
        self.manifest['ContentPackFor']={'UniqueID':'Pathoschild.ContentPatcher'}
        self.write('content.json',{'Changes':[{'Action':'Include','FromFile':'nested.json'}, {'Action':'Include','FromFile':'bad.json'}, {'Action':'Load','Target':'Data/Objects','FromFile':'objects.json'}]})
        self.write('nested.json',{'Changes':[{'Action':'EditData','Target':'Characters/Dialogue/Abigail','When':{'Season':'spring'},'Entries':{'Mon':'Hello farmer!'}}]})
        self.write('objects.json',{'99':{'Name':'Internal_ID','DisplayName':'Pretty Flower','Description':'A flower.','Texture':'assets/a.png','Price':10}})
        (self.folder/'bad.json').write_text('{broken',encoding='utf-8')
        bundle=extract(self.item())
        self.assertEqual(bundle.category,'部分支持')
        self.assertEqual({r.source for r in bundle.records},{'Hello farmer!','Pretty Flower','A flower.'})
        self.assertTrue(bundle.notes)

    def test_outside_include_is_rejected_without_modifying_files(self):
        self.manifest['ContentPackFor']={'UniqueID':'Pathoschild.ContentPatcher'}
        self.write('content.json',{'Changes':[{'Action':'Include','FromFile':'../../outside.json'}]})
        with self.assertRaises(ValueError):extract(self.item())

    def test_dictionary_versions_and_restore_guard_player_changes(self):
        item=self.item()
        def row(text):return {'context':text,'source':text,'source_hash':source_hash(text),'target':'中文'}
        with patch('modlingo.batch.check_game_stopped'):
            receipt=install_dictionary(self.mods,item,[row('old')],self.root/'backup1')
            item.version='2'
            receipt=install_dictionary(self.mods,item,[row('new')],self.root/'backup2')
            data=json.loads(Path(receipt).read_text(encoding='utf-8'));target=Path(data['target'])
            pack=json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual([r['source'] for r in pack['records']],['new'])
            original=target.read_bytes();target.write_text('{}',encoding='utf-8')
            with self.assertRaises(ValueError):restore_dictionary(receipt,data)
            target.write_bytes(original);restore_dictionary(receipt,data)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['mod_version'],'1')

    def test_legacy_manual_fallback_preserved(self):
        file=self.root/'legacy.json'
        Project('Test.Fixture','Fixture',str(self.folder),entries={'a':Entry('Hello','','manual')}).save(file)
        cache=Cache(self.root/'cache');self.addCleanup(cache.close)
        cache.migrate(file)
        self.assertTrue(cache.fallback('Test.Fixture','a','Hello'))
        self.assertFalse(cache.fallback('Test.Fixture','a','Changed'))

    def test_changed_source_retranslates_but_player_override_remains(self):
        self.write('i18n/default.json',{'a':'Hello','b':'Welcome'})
        calls=[]
        async def handler(request):
            calls.append(request)
            return httpx.Response(200,json=envelope(request))
        async def run():
            engine=Engine([self.item()],self.mods,self.config,threading.Event(),self.root/'cache',transport=httpx.MockTransport(handler),use_processes=False)
            return await engine.run()
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            asyncio.run(run())
            pack=json.loads((self.folder/'i18n/zh.json').read_text(encoding='utf-8'));pack['b']='玩家自己的翻译'
            self.write('i18n/zh.json',pack)
            self.write('i18n/default.json',{'a':'Changed greeting','b':'Changed welcome'})
            asyncio.run(run())
        result=json.loads((self.folder/'i18n/zh.json').read_text(encoding='utf-8'))
        self.assertEqual(result['a'],'中文Changed greeting');self.assertEqual(result['b'],'玩家自己的翻译')
        self.assertEqual(len(calls),2)

    def test_capture_metadata_filters_wrong_mod_version_and_hash(self):
        item=self.item()
        record={'mod_id':item.mod_id,'mod_version':'1','context':'gmcm/label','source':'Hello','source_hash':source_hash('Hello'),'adapter':'gmcm'}
        self.assertEqual(len(extract(item,[record]).units),1)
        record['mod_version']='2';self.assertEqual(len(extract(item,[record]).units),0)
        record['mod_version']='1';record['source_hash']='bad';self.assertEqual(len(extract(item,[record]).units),0)

    def test_verification_stop_cancels_local_wait_without_unlocking(self):
        from modlingo.api_verification import verify_connection
        stop=threading.Event();calls=[]
        async def handler(request):
            calls.append(request);stop.set();await asyncio.sleep(5)
            return httpx.Response(200,json=envelope(request))
        started=time.monotonic()
        with self.assertRaises(ValueError):verify_connection(self.config,stop,httpx.MockTransport(handler))
        self.assertLess(time.monotonic()-started,1);self.assertEqual(len(calls),1)

    def test_bridge_player_translation_is_reused_and_midflight_edit_rejected(self):
        from modlingo.cache import digest
        self.manifest['ContentPackFor']={'UniqueID':'Pathoschild.ContentPatcher'}
        self.write('content.json',{'Changes':[{'Action':'EditData','Target':'Characters/Dialogue/Abigail','Entries':{'Mon':'Hello'}}]})
        target=self.mods/'ModLingo.Bridge'/'translations'/(digest(self.manifest['UniqueID'])[:32]+'.json')
        calls=[];edit=False
        async def handler(request):
            calls.append(request)
            if edit:
                pack=json.loads(target.read_text(encoding='utf-8'));pack['records'][0]['target']='翻译时玩家的新修改'
                target.write_text(json.dumps(pack,ensure_ascii=False),encoding='utf-8')
            return httpx.Response(200,json=envelope(request))
        async def run():
            engine=Engine([self.item()],self.mods,self.config,threading.Event(),self.root/'cache',transport=httpx.MockTransport(handler),use_processes=False)
            return await engine.run()
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            asyncio.run(run())
            pack=json.loads(target.read_text(encoding='utf-8'));pack['records'][0]['target']='玩家自己的插件译文'
            target.write_text(json.dumps(pack,ensure_ascii=False),encoding='utf-8')
            asyncio.run(run())
            self.assertEqual(len(calls),1)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['records'][0]['target'],'玩家自己的插件译文')
            self.write('content.json',{'Changes':[{'Action':'EditData','Target':'Characters/Dialogue/Abigail','Entries':{'Mon':'Hello','Tue':'Welcome'}}]})
            edit=True;_,report=asyncio.run(run())
            self.assertEqual(report['success'],[])
            self.assertIn('译文发生变化',report['failure'][0]['reason'])
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['records'][0]['target'],'翻译时玩家的新修改')
