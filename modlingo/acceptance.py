"""Offline acceptance against owned fixtures, never a player's game or API."""
import asyncio
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

import httpx
from . import __version__
from .batch import restore
from .bridge_io import plugin_status
from .core import ProjectError
from .discovery import scan_mods
from .engine import Engine
from .provider import APIConfig


def run_acceptance():
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    with tempfile.TemporaryDirectory(prefix='modlingo-acceptance-') as directory:
        root=Path(directory);mods=root/'Owned sample game'/'Mods';mods.mkdir(parents=True)
        pack=mods/'Sample content';consumer=mods/'Sample destinations'
        write(pack/'manifest.json',{'UniqueID':'ModLingo.Acceptance.Content','Name':'验收样例',
            'Version':'1','ContentPackFor':{'UniqueID':'Pathoschild.ContentPatcher'}})
        write(pack/'i18n/default.json',{'greeting':'Hello, {{PlayerName}}!', 'keep':'An existing translation.'})
        write(pack/'i18n/zh.json',{'keep':'作者已有中文'})
        write(pack/'config.json',{'InternalDestination':'Fixture.Dock','PlayerName':'Private fixture value'})
        write(pack/'content.json',{'Changes':[
            {'Action':'EditData','Target':'Data/Objects','Entries':{'Fixture.Object':{
                'Name':'Internal_Object','DisplayName':'A small flower','Description':'A flower for a friend.','Price':123}}},
            {'Action':'EditData','Target':'Data/Minecarts','Entries':{'Default':{
                'Destinations':[{'Id':'Fixture.Dock','DisplayName':'The pier','TargetLocation':'Town'}]}}}]})
        write(consumer/'manifest.json',{'UniqueID':'jibb.minecarts','Name':'验收目的地',
            'Version':'1','ContentPackFor':{'UniqueID':'Pathoschild.ContentPatcher'}})
        write(consumer/'i18n/default.json',{})
        write(consumer/'content.json',{'Changes':[],'ConfigSchema':{
            '1.':{'AllowValues':'Fixture.Dock','Default':'Fixture.Dock'},
            '2.':{'AllowValues':'Fixture.Dock','Default':'Fixture.Dock'}}})
        write(consumer/'config.json',{'1.':'Fixture.Dock','2.':'Fixture.Dock'})
        unchanged={p:p.read_bytes() for p in (pack/'manifest.json',pack/'content.json',pack/'config.json',
            consumer/'manifest.json',consumer/'content.json',consumer/'config.json')}
        original_zh=(pack/'i18n/zh.json').read_bytes()
        calls=[]

        async def model(request):
            body=json.loads(request.content)
            items=json.loads(body['messages'][-1]['content'])['items']
            result={}
            replacements={'Hello, ':'你好，','Welcome back, ':'欢迎回来，',
                'A small flower':'小花','A flower for a friend.':'送给朋友的一朵花。','The pier':'码头'}
            for key,row in items.items():
                text=row['text'];value=text
                if text=='Fixture.Dock' or 'Private fixture value' in text:
                    raise AssertionError('An internal value reached translation')
                for raw,target in replacements.items():value=value.replace(raw,target)
                if value==text:raise AssertionError('Unexpected fixture prose')
                result[key]=value
            calls.append(len(items))
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop',
                'message':{'content':json.dumps(result,ensure_ascii=False)}}]})

        def translate():
            items=[i for i in scan_mods(mods) if i.mod_id!='ModLingo.Bridge']
            engine=Engine(items,mods,APIConfig('http://127.0.0.1:3333/v1','acceptance'),
                threading.Event(),root/'cache',transport=httpx.MockTransport(model),use_processes=True)
            with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
                _,report=asyncio.run(engine.run())
            if report.get('paused_reason'):raise AssertionError(report['paused_reason'])
            return report

        first=translate()
        assert calls
        assert first['dependencies']['same_run_backfilled_fields']==2
        assert plugin_status(mods)['ready']
        labels=json.loads((consumer/'i18n/zh.json').read_text(encoding='utf-8'))
        assert labels['config.1..values.Fixture.Dock']=='码头'
        assert labels['config.2..values.Fixture.Dock']=='码头'
        installed=(pack/'i18n/zh.json').read_bytes()
        native=json.loads(installed)
        assert native['keep']=='作者已有中文' and '{{PlayerName}}' in native['greeting']
        assert '你好' in native['greeting']
        dictionaries=list((mods/'ModLingo.Bridge/translations').glob('*.json'))
        asset_rows=[record for file in dictionaries for record in json.loads(file.read_text(encoding='utf-8'))['records']]
        assert any(row['path']==['Fixture.Object','DisplayName'] and row['target']=='小花' for row in asset_rows)
        assert all(p.read_bytes()==raw for p,raw in unchanged.items())
        receipts=[Path(receipt) for row in first['success']+first['failure'] for receipt in row.get('receipts',[])]
        assert receipts

        calls.clear();second=translate()
        assert not calls and second['metrics']['requests']==0
        assert (pack/'i18n/zh.json').read_bytes()==installed
        write(pack/'i18n/default.json',{'greeting':'Welcome back, {{PlayerName}}!','keep':'An existing translation.'})
        third=translate()
        assert calls and sum(calls)==1
        native=json.loads((pack/'i18n/zh.json').read_text(encoding='utf-8'))
        assert '欢迎回来' in native['greeting'] and native['keep']=='作者已有中文'
        third_receipts=[Path(receipt) for row in third['success']+third['failure'] for receipt in row.get('receipts',[])]
        with patch('modlingo.batch.check_game_stopped'):
            for receipt in reversed(third_receipts):restore(receipt)
            assert (pack/'i18n/zh.json').read_bytes()==installed
            native_receipt=next(r for r in receipts if Path(json.loads(r.read_text(encoding='utf-8'))['target']).resolve()==(pack/'i18n/zh.json').resolve())
            changed=dict(json.loads(installed),keep='玩家的新编辑')
            write(pack/'i18n/zh.json',changed)
            try:restore(native_receipt)
            except ProjectError:pass
            else:raise AssertionError('A later player edit was overwritten')
            assert json.loads((pack/'i18n/zh.json').read_text(encoding='utf-8'))==changed
            (pack/'i18n/zh.json').write_bytes(installed)
            for receipt in reversed(receipts):restore(receipt)
        assert (pack/'i18n/zh.json').read_bytes()==original_zh
        assert all(p.read_bytes()==raw for p,raw in unchanged.items())
        assert not (consumer/'i18n/zh.json').exists()
        return {'version':__version__,'passed':True,'paid_model_calls':0,
            'transport':'httpx.MockTransport','fixture_only':True,'game_loaded':False,
            'cold_install':True,'bridge_installed':True,'same_run_backfilled_fields':2,
            'warm_requests':0,'changed_source_only_retranslated':True,
            'existing_chinese_preserved':True,'source_and_config_preserved':True,
            'exact_backup_restore':True,'later_player_edit_rejected':True,
            'external_windows_verified':False,'game_display_verified':False}
