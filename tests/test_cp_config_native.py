import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import httpx
from modlingo.discovery import scan_mods
from modlingo.engine import Engine
from modlingo.extraction import extract
from modlingo.provider import APIConfig
from modlingo.memory import memory_key
from modlingo.cache import Cache


class NativeConfigTests(unittest.TestCase):
    def test_pending_native_text_does_not_block_available_bridge_title(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);mods=root/'Mods';folder=mods/'Test';folder.mkdir(parents=True)
            (folder/'manifest.json').write_text(json.dumps(dict(UniqueID='recon88.NoPamEnabling',Name='No Pam Enabling',Version='1',ContentPackFor={'UniqueID':'Pathoschild.ContentPatcher'})))
            (folder/'content.json').write_text(json.dumps({'ConfigSchema':{'UnknownSetting':{'AllowValues':'true,false'}},'Changes':[]}))
            async def forbidden(request):raise AssertionError('network forbidden')
            with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
                _,report=asyncio.run(Engine(scan_mods(mods),mods,APIConfig('http://localhost:3333/v1','fixture'),threading.Event(),root/'cache',use_processes=False,offline_only=True,transport=httpx.MockTransport(forbidden)).run())
            result=(report['success']+report['failure'])[0]
            self.assertEqual(result['status'],'部分完成')
            self.assertGreater(result['installed'],0)
            self.assertFalse((folder/'i18n/zh.json').exists())

    def test_native_labels_preserve_config_and_share_exact_choice_sets(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);mods=root/'Mods';folder=mods/'Test';folder.mkdir(parents=True)
            (folder/'manifest.json').write_text(json.dumps(dict(UniqueID='Test.CP',Name='Test',Version='1',ContentPackFor={'UniqueID':'Pathoschild.ContentPatcher'})))
            source=json.dumps({'Format':'2.0.0','ConfigSchema':{key:{'AllowValues':'Bus,Town','Default':'Bus','Section':'Destinations'} for key in ('First','Second')},'Changes':[]})
            (folder/'content.json').write_text(source)
            (folder/'config.json').write_text('{"First":"Bus","Second":"Town"}')
            original=(folder/'config.json').read_bytes()
            items=scan_mods(mods);bundle=extract(items[0]);cache=Cache(root/'cache')
            cfg=APIConfig('http://127.0.0.1:3333/v1','fixture')
            choices=[r for r in bundle.records if r.source=='Bus']
            self.assertEqual(len(choices),2)
            self.assertEqual(memory_key(cache,cfg,bundle,choices[0]),memory_key(cache,cfg,bundle,choices[1]));cache.close()
            calls=[]
            async def handler(request):
                rows=json.loads(json.loads(request.content)['messages'][-1]['content'])['items'];calls.extend(v['text'] for v in rows.values())
                return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({k:'中文'+v['text'] for k,v in rows.items()})}}]})
            with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
                _,report=asyncio.run(Engine(items,mods,cfg,threading.Event(),root/'cache',use_processes=False,transport=httpx.MockTransport(handler)).run())
            self.assertFalse(report['failure'])
            target=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))
            self.assertEqual(target['config.First.values.Bus'],'中文Bus')
            self.assertEqual(target['config.Second.values.Bus'],'中文Bus')
            self.assertEqual(calls.count('Bus'),1)
            self.assertEqual((folder/'config.json').read_bytes(),original)
            self.assertEqual((folder/'content.json').read_text(),source)

    def test_known_no_pam_aliases_work_without_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            mods=Path(directory);folder=mods/'Test';folder.mkdir()
            (folder/'manifest.json').write_text(json.dumps(dict(UniqueID='recon88.NoPamEnabling',Name='No Pam Enabling',Version='1',ContentPackFor={'UniqueID':'Pathoschild.ContentPatcher'})))
            (folder/'content.json').write_text(json.dumps({'ConfigSchema':{'QuestLetterFrom':{'AllowValues':'Pam, Penny'},'DebugMode_ForceNewQuestLetter':{'AllowValues':'true,false'}},'Changes':[]}))
            bundle=extract(scan_mods(mods)[0]);rows={r.context:r for r in bundle.records}
            self.assertEqual(rows['config.QuestLetterFrom.name'].target,'任务来信寄件人')
            self.assertEqual(rows['config.QuestLetterFrom.values.Penny'].target,'潘妮')
            self.assertEqual(rows['config.DebugMode_ForceNewQuestLetter.name'].target,'调试：强制发送新的任务来信')

    def test_numeric_cp_field_and_existing_default_display_key(self):
        with tempfile.TemporaryDirectory() as directory:
            mods=Path(directory);folder=mods/'Test';(folder/'i18n').mkdir(parents=True)
            (folder/'manifest.json').write_text(json.dumps(dict(UniqueID='7eventy7.StandardizedSeedSprites',Name='Seeds',Version='1',ContentPackFor={'UniqueID':'Pathoschild.ContentPatcher'})))
            content='{"ConfigSchema":{"Vanilla":{"AllowValues":"true,false"}},"Changes":[{"Action":"EditData","Target":"Data/Quests","Fields":{"103":{4:"Pam 350"}}}]}'
            (folder/'content.json').write_text(content)
            (folder/'i18n/default.json').write_text('{"config.Vanilla.name":"Vanilla"}')
            for original,expected in [('Vanilla','原版内容'),('玩家自定义','玩家自定义')]:
                (folder/'i18n/zh.json').write_text(json.dumps({'config.Vanilla.name':original}),encoding='utf-8')
                bundle=extract(scan_mods(mods)[0])
                self.assertFalse(any('未解析' in note for note in bundle.notes))
                self.assertEqual(next(r.target for r in bundle.records if r.context=='config.Vanilla.name'),expected)
                self.assertEqual((folder/'content.json').read_text(),content)
