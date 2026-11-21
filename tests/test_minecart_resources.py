import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from modlingo.cache import Cache
from modlingo.compatibility import minecart_labels
from modlingo.discovery import scan_mods
from modlingo.engine import Engine
from modlingo.extraction import extract, source_hash
from modlingo.minecarts import display_path, resolve_names
from modlingo.provider import APIConfig
from modlingo.workers import prepare_spool, read_spool


class MinecartResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mods=self.root/'Mods';self.mods.mkdir()

    def pack(self,folder='Source',uid='Other.Carts',changes=None,source=None,zh=None,config=None):
        path=self.mods/folder;path.mkdir()
        self.write(path/'manifest.json',{'UniqueID':uid,'Name':folder,'Version':'1',
            'ContentPackFor':{'UniqueID':'Pathoschild.ContentPatcher'}})
        self.write(path/'content.json',{'Changes':changes or [],'ConfigSchema':config or {}})
        if source is not None:self.write(path/'i18n/default.json',source)
        if zh is not None:self.write(path/'i18n/zh.json',zh)
        return path

    def write(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')

    def definition(self,label='Farm',identifier='Mod.Dock',network='Default',**kwargs):
        return dict({'Action':'EditData','Target':'Data/Minecarts','Entries':{
            network:{'Destinations':[{'Id':identifier,'DisplayName':label}]}}},**kwargs)

    def bundles(self,captures=None):return [extract(i,captures) for i in scan_mods(self.mods)]

    def consumer(self):
        p=self.pack('Consumer','jibb.minecarts',source={},config={
            '1.':{'AllowValues':'Mod.Dock','Default':'Mod.Dock'},
            '2.':{'AllowValues':'Mod.Dock','Default':'Mod.Dock'}})
        self.write(p/'config.json',{'1.':'Mod.Dock','2.':'Mod.Dock'})
        return p

    def test_typed_nested_fields_exclude_internal_values_and_custom_metadata(self):
        self.pack(changes=[{'Action':'EditData','Target':'data/minecarts','Entries':{
            'Default':{'LockedMessage':'Come back later','ChooseDestinationMessage':'Where to?',
            'BuyTicketMessage':'Buy a ticket?','UnlockCondition':'PLAYER_HAS_MAIL InternalFlag',
            'Destinations':{'Mod.Dock':{'DisplayName':'Dock','BuyTicketMessage':'Pay for dock?',
                'Id':'Internal.ID','TargetLocation':'InternalMap','Condition':'InternalCondition',
                'CustomFields':{'DisplayName':'Do not translate'}}}}}}])
        b=self.bundles()[0]
        self.assertEqual({r.source for r in b.records},{'Come back later','Where to?','Buy a ticket?','Dock','Pay for dock?'})
        self.assertEqual(len(b.minecart_names),1)
        self.assertEqual(b.minecart_names[0]['path'],['Default','Destinations','Mod.Dock','DisplayName'])

    def test_fields_targetfield_and_model_casing_preserve_id_case(self):
        self.pack(changes=[{'Action':'EditData','Target':'Data/Minecarts',
            'TargetField':['Network','destinations'],'Fields':{'CaseSensitive.ID':{'displayname':'The pier'}}}])
        b=self.bundles()[0]
        self.assertEqual(b.records[0].path,['Network','Destinations','CaseSensitive.ID','DisplayName'])
        self.assertEqual(display_path(['Network','Destinations','CaseSensitive.ID','CustomFields','DisplayName']),None)

    def test_load_json_tracks_definition_and_payload_hashes(self):
        p=self.pack(changes=[{'Action':'Load','Target':'Data/Minecarts','FromFile':'data/carts.json'}])
        self.write(p/'data/carts.json',{'Default':{'Destinations':{'Mod.Dock':{'DisplayName':'Dock'}}}})
        b=self.bundles()[0]
        self.assertIn('data\\carts.json' if '\\' in str(p) else 'data/carts.json',b.source_snapshots)
        self.assertEqual(b.minecart_names[0]['source'],'Dock')

    def test_cross_pack_i18n_name_is_reused_offline_and_source_values_stay_identical(self):
        consumer=self.consumer()
        source=self.pack(changes=[self.definition('{{i18n: place.DOCK}}')],
            source={'Place.Dock':'The pier'},zh={'Place.Dock':'码头'})
        originals={p:p.read_bytes() for folder in (source,consumer) for p in (folder/'content.json',folder/'config.json') if p.exists()}
        async def forbidden(request):raise AssertionError('No identifier or known label may reach API')
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            _,report=asyncio.run(Engine(scan_mods(self.mods),self.mods,APIConfig('http://localhost:3333/v1','fixture'),
                threading.Event(),self.root/'cache',transport=httpx.MockTransport(forbidden),use_processes=False,offline_only=True).run())
        self.assertEqual(report['metrics']['requests'],0)
        language=json.loads((consumer/'i18n/zh.json').read_text(encoding='utf-8'))
        self.assertEqual(language['config.1..values.Mod.Dock'],'码头')
        self.assertEqual(language['config.2..values.Mod.Dock'],'码头')
        for path,raw in originals.items():self.assertEqual(path.read_bytes(),raw)

    def test_cached_asset_label_resolves_without_translating_identifier(self):
        self.consumer();self.pack(changes=[self.definition('The pier')])
        bundles=self.bundles();b=next(b for b in bundles if b.item.mod_id=='Other.Carts')
        config=APIConfig('http://localhost:3333/v1','fixture');cache=Cache(self.root/'cache')
        self.addCleanup(cache.close)
        for unit in b.units:cache.put(cache.key(config,unit.mod_id,unit.context,unit.source,{}),'码头')
        names=resolve_names(bundles,cache,config)
        self.assertEqual(names.names['Mod.Dock'][0],'码头')
        consumer=next(b for b in bundles if b.item.mod_id=='jibb.minecarts')
        self.assertEqual(len(minecart_labels(consumer,names)[0]),2)

    def test_localized_reference_requires_matching_current_chinese_observation(self):
        raw='[LocalizedText Strings\\Locations:MineCart_Destination_BusStop]'
        self.pack(changes=[self.definition(raw)])
        path=['Default','Destinations','Mod.Dock','DisplayName']
        capture={'mod_id':'Other.Carts','mod_version':'1','adapter':'asset','asset':'Data/Minecarts',
            'context':'Data/Minecarts/'+'/'.join(path),'path':path,'source':raw,'source_hash':source_hash(raw),
            'resolved_name':'巴士站','resolved_hash':source_hash('巴士站'),'locale':'zh'}
        b=self.bundles([capture])[0]
        self.assertEqual(b.records,[])
        self.assertEqual(resolve_names([b]).names['Mod.Dock'][0],'巴士站')
        for bad in ({'locale':'en'},{'resolved_hash':'bad'},{'mod_version':'old'},{'source':raw+' changed'}):
            self.assertNotIn('Mod.Dock',resolve_names(self.bundles([dict(capture,**bad)])).names)

    def test_custom_name_and_unknown_tokens_never_become_translation_units(self):
        self.pack(changes=[self.definition('{{CustomCart.Name}}')])
        path=['Default','Destinations','Mod.Dock','DisplayName']
        capture={'mod_id':'Other.Carts','mod_version':'1','adapter':'asset','asset':'Data/Minecarts',
            'context':'Data/Minecarts/'+'/'.join(path),'path':path,'source':'Player private name',
            'source_hash':source_hash('Player private name')}
        b=self.bundles([capture])[0]
        self.assertEqual(b.records,[]);self.assertEqual(b.units,[])
        self.assertIn('Mod.Dock',resolve_names([b]).unresolved)

    def test_player_and_unsupported_game_tokens_are_not_treated_as_names(self):
        for number,text in enumerate(('[PlayerName]','[LocationName Town]','[LocalizedText Strings/Locations:Key argument]')):
            self.pack('Source'+str(number),uid='Pack.'+str(number),changes=[self.definition(text)])
        for bundle in self.bundles():
            self.assertEqual(bundle.units,[])
            self.assertIn('Mod.Dock',resolve_names([bundle]).unresolved)

    def test_conflicting_networks_or_unknown_conditional_definition_stay_pending(self):
        for alternate in ('另一个码头','{{CustomCart.Name}}'):
            with self.subTest(alternate=alternate):
                p=self.pack('Source'+str(len(list(self.mods.iterdir()))),uid='Pack.'+str(len(list(self.mods.iterdir()))),
                    changes=[self.definition('码头'),self.definition(alternate,network='Other')])
                names=resolve_names([extract(next(i for i in scan_mods(self.mods) if i.folder==p.resolve()))])
                self.assertNotIn('Mod.Dock',names.names)
                self.assertIn('Mod.Dock',names.unresolved)

    def test_manual_fallback_and_stale_original_name_are_not_reused(self):
        self.pack(changes=[self.definition('{{i18n: Name}}')],source={'Name':'Pier'},zh={'Name':'码头'})
        b=self.bundles()[0];record=next(r for r in b.records if r.context=='Name')
        record.manual_fallback=True
        self.assertNotIn('Mod.Dock',resolve_names([b]).names)
        del record.manual_fallback
        cache=Cache(self.root/'cache');self.addCleanup(cache.close)
        cache.db.execute('INSERT INTO baseline VALUES (?,?,?,?)',(str(b.item.folder),'Name','Old pier','码头'));cache.db.commit()
        self.assertNotIn('Mod.Dock',resolve_names([b],cache,APIConfig('http://localhost:3333/v1','fixture')).names)

    def test_spool_preserves_reference_and_observation_metadata(self):
        self.pack(changes=[self.definition('码头')]);item=scan_mods(self.mods)[0]
        path=self.root/'spool.jsonl';prepare_spool(item,self.root/'cache',path,[])
        b=read_spool(item,path)
        self.assertEqual(resolve_names([b]).names['Mod.Dock'][0],'码头')

    def test_destination_list_reordering_and_ambiguous_ids(self):
        p=self.pack(changes=[self.definition('码头')])
        changes=[self.definition('码头')]
        destinations=changes[0]['Entries']['Default']['Destinations']
        destinations.append({'Id':'Other.ID','DisplayName':'农场'})
        self.write(p/'content.json',{'Changes':changes});a=self.bundles()[0]
        destinations.reverse();self.write(p/'content.json',{'Changes':changes});b=self.bundles()[0]
        self.assertEqual(sorted((r.context,r.source) for r in a.records),sorted((r.context,r.source) for r in b.records))
        destinations.append({'Id':'Mod.Dock','DisplayName':'另一码头'})
        self.write(p/'content.json',{'Changes':changes});b=self.bundles()[0]
        self.assertEqual(b.records,[])
        self.assertIn('Mod.Dock',resolve_names([b]).unresolved)

    def test_changed_provider_stops_consumer_install(self):
        consumer=self.consumer()
        source=self.pack(changes=[self.definition('码头')])
        def notify(kind,data):
            if kind=='engine_progress' and data.get('phase')=='保存/安装已校验结果':
                self.write(source/'content.json',{'Changes':[self.definition('变更后的码头')]})
        async def forbidden(request):raise AssertionError('No paid requests')
        with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
            _,report=asyncio.run(Engine(scan_mods(self.mods),self.mods,APIConfig('http://localhost:3333/v1','fixture'),
                threading.Event(),self.root/'cache',notify=notify,transport=httpx.MockTransport(forbidden),use_processes=False,offline_only=True).run())
        row=next(r for r in report['failure'] if r['mod_id']=='jibb.minecarts')
        self.assertEqual(row['status'],'文件异常')
        self.assertFalse((consumer/'i18n/zh.json').exists())

    def test_unknown_asset_prefix_and_custom_path_do_not_get_extracted(self):
        self.pack(changes=[dict(self.definition('Not text'),Target='Data/MinecartsUnknown'),
            {'Action':'EditData','Target':'Data/Minecarts','Entries':{'Default':{'CustomFields':{'DisplayName':'Not a label'}}}}])
        b=self.bundles()[0]
        self.assertEqual(b.records,[]);self.assertEqual(b.minecart_names,[])


if __name__=='__main__':unittest.main()
