import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import httpx
from modlingo.discovery import scan_mods
from modlingo.extraction import extract,unrelated_language,source_hash
from modlingo.engine import Engine
from modlingo.provider import APIConfig
from modlingo.workers import prepare_spool,read_spool


class LanguageScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mods=self.root/'Mods';self.folder=self.mods/'Fixture';self.folder.mkdir(parents=True)
        (self.folder/'manifest.json').write_text(json.dumps({'UniqueID':'Test.Language','Name':'Language fixture','Version':'1','ContentPackFor':{'UniqueID':'Pathoschild.ContentPatcher'}}),encoding='utf-8')

    def write(self,changes,filename='content.json'):
        (self.folder/filename).write_text(json.dumps({'Changes':changes}),encoding='utf-8')

    def text(self,condition=None):
        row={'Action':'EditData','Target':'Characters/Dialogue/Abigail','Entries':{'Test':'Hello farmer'}}
        if condition is not None:row['When']={'Language':condition}
        return row

    def bundle(self):return extract(scan_mods(self.mods)[0])

    def test_only_known_foreign_literal_conditions_are_pruned(self):
        for language in ('fr','ru','es,pt','ja,ko'):
            self.assertTrue(unrelated_language(self.text(language)))
        for language in ('en','zh','fr,zh','es,en','{{ChosenLanguage}}','!en','custom','en-US',['fr'],False,''):
            self.assertEqual(unrelated_language(self.text(language)),'')

    def test_explicit_locale_is_conservative(self):
        for locale in ('fr','es','ru'):
            self.assertTrue(unrelated_language(dict(self.text(),TargetLocale=locale)))
        for locale in ('','en','zh','{{Language}}','fr,es','custom',['fr']):
            self.assertEqual(unrelated_language(dict(self.text(),TargetLocale=locale)),'')

    def test_excluded_only_mod_is_separate_from_success_and_materials(self):
        self.write([self.text('fr')])
        bundle=self.bundle()
        self.assertEqual(bundle.records,[])
        self.assertEqual(bundle.category,'仅其他语言')
        self.assertEqual(len(bundle.excluded_patches),1)
        self.assertEqual(bundle.notes,[])

    def test_mixed_patch_retains_english_and_chinese(self):
        self.write([self.text('fr'),self.text('en'),self.text('zh')])
        bundle=self.bundle()
        self.assertEqual(len(bundle.records),2)
        self.assertEqual(bundle.category,'可直接翻译')
        self.assertEqual(len(bundle.excluded_patches),1)

    def test_inactive_include_does_not_poison_shared_include_visitation(self):
        self.write([{'Action':'Include','FromFile':'shared.json','When':{'Language':'fr'}},
                    {'Action':'Include','FromFile':'shared.json'}])
        self.write([self.text()],'shared.json')
        bundle=self.bundle()
        self.assertEqual(len(bundle.records),1)
        self.assertEqual(len(bundle.excluded_patches),1)

    def test_unknown_condition_and_unknown_action_are_not_skipped(self):
        self.write([dict(self.text('fr'),Action='CustomAction')])
        self.assertTrue(self.bundle().notes)
        self.write([self.text('{{LanguageToken}}')])
        self.assertEqual(len(self.bundle().records),1)

    def test_spool_preserves_exclusion_details(self):
        self.write([self.text('fr')])
        item=scan_mods(self.mods)[0]
        spool=prepare_spool(item,self.root/'cache',self.root/'spool.jsonl',[])
        bundle=read_spool(item,spool)
        self.assertEqual(len(bundle.excluded_patches),1)
        self.assertEqual(bundle.category,'仅其他语言')

    def test_actual_game_capture_is_not_hidden_by_static_language_exclusion(self):
        self.write([self.text('fr')])
        item=scan_mods(self.mods)[0]
        source='Actual visible English'
        capture={'mod_id':item.mod_id,'mod_version':'1','adapter':'asset','asset':'Characters/Dialogue/Abigail',
                 'path':['Test'],'context':'Characters/Dialogue/Abigail/Test','source':source,'source_hash':source_hash(source)}
        bundle=extract(item,[capture])
        self.assertEqual([r.source for r in bundle.records],[source])
        self.assertEqual(bundle.category,'可直接翻译')
        self.assertEqual(len(bundle.excluded_patches),1)

    def test_foreign_text_does_not_hide_unvisited_gmcm_configuration(self):
        self.write([self.text('fr')])
        path=self.folder/'content.json';data=json.loads(path.read_text(encoding='utf-8'))
        data['ConfigSchema']={'Enabled':{'AllowValues':'true,false','Default':'true'}}
        path.write_text(json.dumps(data),encoding='utf-8')
        bundle=self.bundle()
        self.assertEqual(bundle.category,'可直接翻译')
        self.assertIn('config.Enabled.name',[r.context for r in bundle.records])

    def test_foreign_patch_does_not_hide_unknown_xnb_resource(self):
        self.write([self.text('fr')])
        (self.folder/'unknown.xnb').write_bytes(b'')
        self.assertEqual(self.bundle().category,'需进游戏采集')

    def test_engine_does_not_call_api_or_modify_source_for_excluded_only_mod(self):
        self.write([self.text('fr')])
        original=(self.folder/'content.json').read_bytes();calls=[]
        async def handler(request):calls.append(request);raise AssertionError('No API expected')
        async def run():
            with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
                engine=Engine(scan_mods(self.mods),self.mods,APIConfig('http://127.0.0.1:3333/v1','fixture'),threading.Event(),self.root/'cache',transport=httpx.MockTransport(handler),use_processes=False)
                return await engine.run()
        _,report=asyncio.run(run())
        self.assertEqual(calls,[])
        self.assertEqual(report['success'],[])
        self.assertEqual(report['failure'][0]['status'],'仅其他语言')
        self.assertEqual(len(report['failure'][0]['excluded_patches']),1)
        self.assertEqual((self.folder/'content.json').read_bytes(),original)
