import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import httpx
from modlingo.async_provider import Session,translate_recover
from modlingo.core import ProjectError
from modlingo.mod_names import display_alias
from modlingo.provider import APIConfig
from modlingo.test_snapshot import freeze_sources,verify_snapshot

class LimitedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mods=self.root/'Mods';self.folder=self.mods/'A'
        (self.folder/'i18n').mkdir(parents=True)
        (self.folder/'manifest.json').write_text('{"UniqueID":"Test.A","Name":"A","Version":"1"}')
        (self.folder/'i18n/default.json').write_text('{"hello":"Hello farmer"}')
        (self.folder/'i18n/zh.json').write_text('{"hello":"already paid"}')

    def test_snapshot_excludes_existing_targets_and_binary_files(self):
        (self.folder/'Private.dll').write_bytes(b'do not copy')
        (self.folder/'config.json').write_text('{"private":"do not copy"}')
        destination=self.root/'snapshot'
        result=freeze_sources(self.mods,destination)
        verify_snapshot(destination)
        self.assertFalse(any(destination.rglob('zh.json')))
        self.assertFalse(any(destination.rglob('*.dll')))
        self.assertFalse(any(destination.rglob('config.json')))
        self.assertEqual(result['inventory'][0]['records'],1)

    def test_snapshot_hash_detects_changed_source(self):
        destination=self.root/'snapshot';freeze_sources(self.mods,destination)
        next(destination.rglob('default.json')).write_text('{}')
        with self.assertRaises(ProjectError):verify_snapshot(destination)

    def test_preflight_retains_original_extraction_failure(self):
        from modlingo.test_preflight import forecast
        destination=self.root/'snapshot';freeze_sources(self.mods,destination)
        inventory=destination/'inventory.json';data=json.loads(inventory.read_text(encoding='utf-8'))
        data['inventory'][0]['category']='文件异常';data['inventory'][0]['notes']=['original failure']
        inventory.write_text(json.dumps(data),encoding='utf-8')
        output=self.root/'round-1-test';output.mkdir()
        (output/'sample-sources.json').write_text(json.dumps([['test','Hello farmer']]),encoding='utf-8')
        (output/'result.json').write_text(json.dumps({'all_sample_passed':True,'wire_profile':'deepseek-compact-v1','budget':{'conservative_cny':.0001}}),encoding='utf-8')
        result=forecast(destination,self.root)
        self.assertFalse(result['cold_acceptance_allowed'])
        self.assertTrue(any(row['category']=='文件异常' for row in result['coverage_gaps']))

    def test_fixed_parser_gap_is_not_frozen_as_permanent_failure(self):
        from modlingo.test_preflight import forecast
        destination=self.root/'snapshot';freeze_sources(self.mods,destination)
        inventory=destination/'inventory.json';data=json.loads(inventory.read_text(encoding='utf-8'))
        data['inventory'][0]['category']='部分支持';data['inventory'][0]['notes']=['old parser gap']
        inventory.write_text(json.dumps(data),encoding='utf-8')
        output=self.root/'round-1-test';output.mkdir()
        (output/'sample-sources.json').write_text(json.dumps([['test','Hello farmer']]),encoding='utf-8')
        (output/'result.json').write_text(json.dumps({'all_sample_passed':True,'wire_profile':'deepseek-compact-v1','budget':{'conservative_cny':.0001}}),encoding='utf-8')
        result=forecast(destination,self.root)
        self.assertEqual(result['coverage_gaps'],[])
        self.assertTrue(result['cold_acceptance_allowed'])

    def test_alias_requires_exact_mod_identity_and_original_name(self):
        self.assertIn('核心框架',display_alias('spacechase0.SpaceCore','SpaceCore'))
        self.assertEqual(display_alias('Other.Mod','SpaceCore'),'')
        self.assertEqual(display_alias('spacechase0.SpaceCore','New Name'),'')

    def test_unknown_names_are_terminal_without_claiming_translation_success(self):
        async def run():
            calls=[];success=[];errors=[]
            async def handler(request):
                calls.append(request)
                records=json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
                return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({key:value['text'] for key,value in records.items()})}}]})
            config=APIConfig('http://127.0.0.1:3333/v1','fixture')
            async with Session(config,transport=httpx.MockTransport(handler)) as session:
                session.contexts={'brand1':'Test.A | gmcm/mod-name','brand2':'Test.B | gmcm/mod-name'}
                await translate_recover(session,[('brand1','UnknownBrand'),('brand2','OtherBrand')],{},
                    lambda rows,usage:success.extend(rows),lambda rows,reason:errors.extend((key,reason) for key,raw in rows))
            self.assertEqual(len(calls),1);self.assertEqual(success,[])
            self.assertEqual(len(errors),2)
            self.assertTrue(all('需中文别名' in reason for key,reason in errors))
        asyncio.run(run())
