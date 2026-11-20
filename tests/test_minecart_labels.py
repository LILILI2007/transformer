import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import httpx
from modlingo.compatibility import minecart_labels
from modlingo.discovery import scan_mods
from modlingo.extraction import extract
from modlingo.engine import Engine
from modlingo.provider import APIConfig


class MinecartLabelTests(unittest.TestCase):
    def fixture(self,root,mod_id='jibb.minecarts'):
        folder=root/'Mods'/'Minecarts';(folder/'i18n').mkdir(parents=True)
        (folder/'manifest.json').write_text(json.dumps(dict(UniqueID=mod_id,Name='Integrated Minecarts (CP Component)',Version='1',ContentPackFor={'UniqueID':'Pathoschild.ContentPatcher'})))
        (folder/'content.json').write_text(json.dumps({'ConfigSchema':{key:{'AllowValues':'Custom.Dock,Unknown.ID','Default':'Custom.Dock'} for key in ['1.','2.']},'Changes':[]}))
        (folder/'config.json').write_text('{"1.":"Custom.Dock","2.":"Unknown.ID"}')
        (folder/'i18n/default.json').write_text(json.dumps({'Custom.Dock':'Farm'}))
        (folder/'i18n/zh.json').write_text(json.dumps({'Custom.Dock':'农场'}),encoding='utf-8')
        return folder

    def test_offline_reuse_and_unresolved_never_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=self.fixture(root)
            originals={p.name:p.read_bytes() for p in [folder/'config.json',folder/'content.json']}
            async def forbidden(request):raise AssertionError('Identifier must not reach API')
            with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
                _,report=asyncio.run(Engine(scan_mods(root/'Mods'),root/'Mods',APIConfig('http://localhost:3333/v1','fixture'),threading.Event(),root/'cache',use_processes=False,transport=httpx.MockTransport(forbidden),retry_exhausted=True).run())
            row=(report['success']+report['failure'])[0]
            target=json.loads((folder/'i18n/zh.json').read_text(encoding='utf-8'))
            for key in ['1.','2.']:self.assertEqual(target['config.'+key+'.values.Custom.Dock'],'农场')
            self.assertEqual(report['metrics']['requests'],0)
            self.assertEqual(len(row['display_label_reuse']),2)
            self.assertEqual(row['coverage']['pending_records'],2)
            self.assertTrue(all(x['code']=='alias_required' for x in row['diagnostics']))
            for name,raw in originals.items():self.assertEqual((folder/name).read_bytes(),raw)

    def test_scope_manual_and_conflicting_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=self.fixture(root)
            item=scan_mods(root/'Mods')[0];bundle=extract(item)
            resolved,pending=minecart_labels(bundle)
            self.assertEqual(len(resolved),2)
            next(r for r in bundle.records if r.context=='Custom.Dock').manual_fallback=True
            self.assertFalse(minecart_labels(bundle)[0])
            bundle=extract(item)
            choice=next(r for r in bundle.records if r.context=='config.1..values.Custom.Dock')
            choice.target='我的农场'
            self.assertNotIn(bundle.records.index(choice),minecart_labels(bundle)[0])
            bundle.item.mod_id='Other.Mod'
            self.assertEqual(minecart_labels(bundle),({},set()))

    def test_ambiguous_display_name_does_not_choose_one(self):
        from modlingo.extraction import Record
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.fixture(root);bundle=extract(scan_mods(root/'Mods')[0])
            bundle.records.extend([Record('config.EnableCustom.Dock.name','Farm area','text','i18n','i18n',target='农场区域'),Record('config.EnableCustom.Dock.description','Custom.Dock','text','i18n','i18n')])
            self.assertFalse(minecart_labels(bundle)[0])


if __name__=='__main__':unittest.main()
