import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import httpx
from modlingo.discovery import scan_mods
from modlingo.extraction import extract,source_hash
from modlingo.engine import Engine
from modlingo.provider import APIConfig
from modlingo.workers import prepare_spool,read_spool

class MixedI18nTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mods=self.root/'Mods';self.folder=self.mods/'Fixture';self.folder.mkdir(parents=True)
        (self.folder/'manifest.json').write_text(json.dumps({'UniqueID':'Test.Mixed','Name':'Mixed fixture','Version':'1','ContentPackFor':{'UniqueID':'Pathoschild.ContentPatcher'}}),encoding='utf-8')
        self.source='pause 1/message "{{i18n:intro}}"/end dialogue Abigail "Literal goodbye"'
        self.write(self.source)

    def write(self,value):
        (self.folder/'content.json').write_text(json.dumps({'Changes':[{'Action':'EditData','Target':'Data/Events/Town','Entries':{'Event':value}}]}),encoding='utf-8')

    def item(self):return scan_mods(self.mods)[0]

    def capture(self,**kwargs):
        source=self.source.replace('{{i18n:intro}}','Resolved introduction')
        return dict({'mod_id':'Test.Mixed','mod_version':'1','adapter':'asset','asset':'Data/Events/Town','path':['Event'],'context':'Data/Events/Town/Event','source':source,'source_hash':source_hash(source)},**kwargs)

    def test_mixed_script_is_pending_not_direct_success(self):
        bundle=extract(self.item())
        self.assertEqual(bundle.category,'需进游戏采集')
        self.assertEqual(bundle.records,[])
        self.assertEqual(len(bundle.pending_captures),1)
        self.assertFalse(bundle.pending_captures[0]['observed_path'])
        self.assertEqual(bundle.pending_captures[0]['path'],['Event'])

    def test_only_i18n_references_and_control_tokens_do_not_create_false_gaps(self):
        for value in ('{{i18n:intro}}','pause 1/message "{{i18n:intro}}"/end dialogue Abigail "{{i18n:closing}}"'):
            self.write(value)
            self.assertEqual(extract(self.item()).pending_captures,[])

    def test_control_ids_outside_human_fields_are_not_pending_text(self):
        self.write('pause 1/message "{{i18n:intro}}"/addItem InternalEnglishID/end')
        self.assertEqual(extract(self.item()).pending_captures,[])

    def test_valid_capture_marks_observed_path_without_claiming_other_variants(self):
        b=extract(self.item(),[self.capture()])
        self.assertTrue(b.pending_captures[0]['observed_path'])
        self.assertEqual(b.category,'部分支持')
        self.assertTrue(any(r.captured for r in b.records))

    def test_wrong_version_hash_path_or_unresolved_source_does_not_count(self):
        unresolved=self.source
        for row in (self.capture(mod_version='2'),self.capture(source_hash='bad'),self.capture(path=['Other']),
                    self.capture(source=unresolved,source_hash=source_hash(unresolved))):
            self.assertFalse(extract(self.item(),[row]).pending_captures[0]['observed_path'])

    def test_spool_preserves_pending_details_and_reads_old_metadata(self):
        item=self.item();path=Path(prepare_spool(item,self.root/'cache',self.root/'spool.jsonl',[]))
        self.assertEqual(len(read_spool(item,path).pending_captures),1)
        lines=path.read_text(encoding='utf-8').splitlines();meta=json.loads(lines[0]);meta.pop('pending_captures')
        lines[0]=json.dumps(meta);path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
        self.assertEqual(read_spool(item,path).pending_captures,[])

    def run_engine(self):
        calls=[]
        async def handler(request):calls.append(request);raise AssertionError('No paid or simulated request needed')
        async def run():
            with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
                engine=Engine(scan_mods(self.mods),self.mods,APIConfig('http://127.0.0.1:3333/v1','fixture'),threading.Event(),self.root/'cache',transport=httpx.MockTransport(handler),use_processes=False)
                return await engine.run()
        _,report=asyncio.run(run());self.assertEqual(calls,[]);return report

    def test_no_text_result_keeps_pending_location_and_hint(self):
        report=self.run_engine();self.assertFalse(report['success'])
        row=report['failure'][0]
        self.assertEqual(row['status'],'需进游戏采集')
        self.assertEqual(len(row['pending_captures']),1)
        self.assertIn('Bridge',row['reason'])

    def test_completed_i18n_is_partial_when_mixed_script_is_unobserved(self):
        (self.folder/'i18n').mkdir()
        for name,value in [('default','English intro'),('zh','玩家已有中文')]:
            (self.folder/('i18n/'+name+'.json')).write_text(json.dumps({'intro':value},ensure_ascii=False),encoding='utf-8')
        before=(self.folder/'content.json').read_bytes()
        report=self.run_engine();self.assertFalse(report['success'])
        row=report['failure'][0];self.assertEqual(row['status'],'部分完成')
        self.assertEqual(row['coverage']['pending_capture_fields'],1)
        self.assertEqual(row['coverage']['observed_capture_paths'],0)
        self.assertIn('混合 i18n',row['reason'])
        self.assertEqual((self.folder/'content.json').read_bytes(),before)
