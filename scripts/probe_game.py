"""Opt-in isolated title-screen integration probe, without opening a save/API."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from modlingo.batch import check_game_stopped
from modlingo.cache import digest
from modlingo.extraction import source_hash
from modlingo.extraction import extract
from modlingo.discovery import scan_mods
from modlingo import __version__
from modlingo.syntax import spans,rebuild

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--game-path',required=True)
    parser.add_argument('--real-mods-from')
    args=parser.parse_args()
    check_game_stopped()
    game=Path(args.game_path).resolve()
    root=Path(__file__).resolve().parent.parent
    profile=root/'artifacts'/('bridge-probe-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    mods=profile/'Mods';mods.mkdir(parents=True,exist_ok=False)
    for name in ('ContentPatcher','GenericModConfigMenu'):
        shutil.copytree(game/'Mods'/name,mods/name)
    bridge=mods/'ModLingo.Bridge';bridge.mkdir()
    shutil.copy2(root/'bridge/bin/Release/net6.0/ModLingo.Bridge.dll',bridge/'ModLingo.Bridge.dll')
    shutil.copy2(root/'bridge/manifest.json',bridge/'manifest.json')
    shutil.copy2(root/'bridge/package/names.zh.json',bridge/'names.zh.json')
    shutil.copy2(root/'bridge/package/display.zh.json',bridge/'display.zh.json')
    fixture=mods/'ModLingo.Probe';fixture.mkdir()
    shutil.copy2(root/'bridge/probe/bin/Release/net6.0/ModLingo.Probe.dll',fixture/'ModLingo.Probe.dll')
    (fixture/'manifest.json').write_text(json.dumps({'Name':'ModLingo integration probe','Author':'ModLingo','Version':'1.0.0','UniqueID':'ModLingo.Probe','EntryDll':'ModLingo.Probe.dll','Dependencies':[{'UniqueID':n} for n in ('ModLingo.Bridge','spacechase0.GenericModConfigMenu')]}),encoding='utf-8')
    cp=mods/'ModLingo.ProbeCP';cp.mkdir()
    (cp/'manifest.json').write_text(json.dumps({'Name':'ModLingo asset probe','Author':'ModLingo','Version':'1.0.0','UniqueID':'ModLingo.ProbeCP','ContentPackFor':{'UniqueID':'Pathoschild.ContentPatcher'}}),encoding='utf-8')
    (cp/'content.json').write_text(json.dumps({'Format':'2.0.0','Changes':[{'Action':'EditData','Target':'Characters/Dialogue/Abigail','Entries':{'ModLingoProbe':'Hello asset fixture'}}]}),encoding='utf-8')
    cp_data=json.loads((cp/'content.json').read_text(encoding='utf-8'))
    event_source='pause 1/quickQuestion Help me?#Yes#No(break)message "Thank you!"\\pause 100(break)switchEvent KeepBranchID/end'
    event_parts,event_notes=spans(event_source,'event')
    assert not event_notes
    event_target=rebuild(event_source,event_parts,{'0':'帮帮我？','1':'好的','2':'不了','3':'谢谢你！'})
    cp_data['Changes'].append({'Action':'EditData','Target':'Data/Events/Town','Entries':{'ModLingoProbeQuestion':event_source}})
    bubble_source='pause 1/textAboveHead ModLingoBubbleActor "Hello bubble!"/end'
    bubble_parts,bubble_notes=spans(bubble_source,'event')
    assert not bubble_notes
    bubble_target=rebuild(bubble_source,bubble_parts,{'0':'你好气泡！'})
    cp_data['Changes'].append({'Action':'EditData','Target':'Data/Events/Town','Entries':{'ModLingoProbeBubble':bubble_source}})
    header_source='none/-100 -100/farmer 12 12 1/message "Hello header fixture!"/end'
    header_parts,header_notes=spans(header_source,'event')
    assert not header_notes
    header_target=rebuild(header_source,header_parts,{'0':'你好剧情头部测试！'})
    cp_data['Changes'].append({'Action':'EditData','Target':'Data/Events/Town','Entries':{'ModLingoProbeHeader':header_source}})
    closing_source='pause 1/end dialogue Abigail "See you tomorrow!"'
    closing_parts,closing_notes=spans(closing_source,'event')
    assert not closing_notes
    closing_target=rebuild(closing_source,closing_parts,{'0':'明天见！'})
    cp_data['Changes'].append({'Action':'EditData','Target':'Data/Events/Town','Entries':{'ModLingoProbeClosing':closing_source}})
    (cp/'i18n').mkdir()
    (cp/'i18n/default.json').write_text(json.dumps({'opening':'Localized opening fixture'}),encoding='utf-8')
    mixed_source='pause 1/message "{{i18n:opening}}"/end dialogue Abigail "Literal closing fixture"'
    cp_data['Changes'].append({'Action':'EditData','Target':'Data/Events/Town','Entries':{'ModLingoProbeMixedClosing':mixed_source}})
    cp_data['Changes'].extend([
        {'Action':'EditData','Target':'Data/WorldMap','Entries':{'ModLingoProbeRegion':{'MapAreas':[
            {'Id':'second','ScrollText':'Probe map scroll','Tooltips':[]},
            {'Id':'area','ScrollText':'Probe map scroll','PixelArea':{'X':11,'Y':22,'Width':33,'Height':44},'Tooltips':[{'Id':'tip','Text':'Original map tooltip','Condition':'TRUE','KnownCondition':'TRUE','LeftNeighbor':'Neighbor'}]}]}}},
        {'Action':'EditData','Target':'Data/WorldMap','TargetField':['ModLingoProbeRegion','MapAreas','area','ToolTips','tip'],'Entries':{'Text':'Nested map tooltip'}}])
    cp_data['Changes'].extend([
        {'Action':'EditData','Target':'Characters/Dialogue/Abigail','Entries':{'ModLingoProbeLanguage':'Language base English','ModLingoProbeNativeZh':'Native fallback text'}},
        {'Action':'EditData','Target':'Characters/Dialogue/Abigail','When':{'Language':'fr'},'Entries':{'ModLingoProbeLanguage':'French-only source sentinel'}},
        {'Action':'EditData','Target':'Characters/Dialogue/Abigail','When':{'Language':'zh'},'Entries':{'ModLingoProbeNativeZh':'作者已有中文'}},
        {'Action':'EditData','Target':'Characters/Dialogue/Abigail','When':{'Language':'en'},'Entries':{'ModLingoProbeRetainEn':'English fallback remains extractable'}},
        {'Action':'EditData','Target':'Data/Objects','Entries':{'ModLingoProbeObject':{'Name':'probe_internal_id','DisplayName':'Original object name','Description':'Original object description','Type':'Basic','Category':-999,'Price':123}}},
        {'Action':'EditData','Target':'Data/Objects','TargetField':['ModLingoProbeObject'],'Entries':{'DisplayName':'Nested flower name','Description':'Nested flower description'}}])
    (cp/'content.json').write_text(json.dumps(cp_data),encoding='utf-8')
    cp_item=next(item for item in scan_mods(mods) if item.mod_id=='ModLingo.ProbeCP')
    cp_bundle=extract(cp_item)
    language_extraction_checked=(len(cp_bundle.excluded_patches)==1 and
        not any(r.source=='French-only source sentinel' for r in cp_bundle.records) and
        any(r.source=='English fallback remains extractable' for r in cp_bundle.records) and
        any(r.source=='作者已有中文' for r in cp_bundle.records))
    assert language_extraction_checked
    translations=bridge/'translations';translations.mkdir()
    source='Hello asset fixture'
    pack={'schema_version':1,'mod_id':'ModLingo.ProbeCP','mod_version':'1.0.0','records':[{'adapter':'asset','context':'Characters/Dialogue/Abigail/ModLingoProbe','asset':'Characters/Dialogue/Abigail','path':['ModLingoProbe'],'source':source,'source_hash':source_hash(source),'target':'你好资源测试'}]}
    for key,source,target in [('ModLingoProbeLanguage','Language base English','中文条件基础'),('ModLingoProbeNativeZh','Native fallback text','不应覆盖作者中文')]:
        pack['records'].append({'adapter':'asset','context':'Characters/Dialogue/Abigail/'+key,'asset':'Characters/Dialogue/Abigail','path':[key],'source':source,'source_hash':source_hash(source),'target':target})
    pack['records'].append({'adapter':'asset','context':'Data/Events/Town/ModLingoProbeQuestion','asset':'Data/Events/Town','path':['ModLingoProbeQuestion'],'source':event_source,'source_hash':source_hash(event_source),'target':event_target})
    pack['records'].append({'adapter':'asset','context':'Data/Events/Town/ModLingoProbeBubble','asset':'Data/Events/Town','path':['ModLingoProbeBubble'],'source':bubble_source,'source_hash':source_hash(bubble_source),'target':bubble_target})
    pack['records'].append({'adapter':'asset','context':'Data/Events/Town/ModLingoProbeHeader','asset':'Data/Events/Town','path':['ModLingoProbeHeader'],'source':header_source,'source_hash':source_hash(header_source),'target':header_target})
    pack['records'].append({'adapter':'asset','context':'Data/Events/Town/ModLingoProbeClosing','asset':'Data/Events/Town','path':['ModLingoProbeClosing'],'source':closing_source,'source_hash':source_hash(closing_source),'target':closing_target})
    for path,source,target in [(['ModLingoProbeRegion','MapAreas','area','ScrollText'],'Probe map scroll','中文地图区域'),(['ModLingoProbeRegion','MapAreas','area','Tooltips','tip','Text'],'Nested map tooltip','中文地图提示'),(['ModLingoProbeRegion','MapAreas','area','Tooltips','tip','Text'],'Original map tooltip','不应覆盖新条件原文')]:
        pack['records'].append({'adapter':'asset','context':'Data/WorldMap/'+'/'.join(path),'asset':'Data/WorldMap','path':path,'source':source,'source_hash':source_hash(source),'target':target})
    for field,source,target in [('DisplayName','Nested flower name','嵌套路径花朵'),('Description','Nested flower description','嵌套路径说明')]:
        pack['records'].append({'adapter':'asset','context':'Data/Objects/ModLingoProbeObject/'+field,'asset':'Data/Objects','path':['ModLingoProbeObject',field],'source':source,'source_hash':source_hash(source),'target':target})
    (translations/(digest('ModLingo.ProbeCP')[:32]+'.json')).write_text(json.dumps(pack,ensure_ascii=False),encoding='utf-8')
    fixture_records=[{'adapter':'gmcm','context':'gmcm/AddParagraph/text','asset':'','path':[],'source':'Hello GMCM fixture','source_hash':source_hash('Hello GMCM fixture'),'target':'你好设置菜单测试'}]
    for context,source,target in [
        ('gmcm/runtime/SimpleModOption/name/probe-runtime-bool','DebugMode_ForceNewQuestLetter','调试：强制新任务信'),
        ('gmcm/runtime/ChoiceModOption/name/probe-runtime-choice','QuestLetterFrom','任务信寄件人'),
        ('gmcm/runtime/ChoiceModOption/tooltip/probe-runtime-choice','Choose who sends the quest letter','选择任务信的寄件人'),
        ('gmcm/runtime/SectionTitleModOption/name','Mod Section Toggles','模组分组开关'),
        ('gmcm/runtime/pageTitle/Advanced Settings','Advanced Settings','高级设置'),
        ('gmcm/AddTextOption/formatAllowedValue/probe-runtime-choice','jibb.Farm','农场'),
        ('gmcm/AddTextOption/formatAllowedValue/probe-runtime-choice','Bus','巴士站')]:
        fixture_records.append({'adapter':'gmcm','context':context,'asset':'','path':[],'source':source,'source_hash':source_hash(source),'target':target})
    for context,source,target in [('gmcm/mod-name','ModLingo integration probe','模组语言集成探针'),('gmcm/AddTextOption/formatAllowedValue/probe-choice','Always show','始终显示'),('gmcm/AddTextOption/formatAllowedValue','Seasonal mode','季节模式'),('gmcm/AddNumberOption/formatValue/int/probe-days','{{ML_NUMBER}} days','{{ML_NUMBER}} 天')]:
        fixture_records.append({'adapter':'gmcm','context':context,'asset':'','path':[],'source':source,'source_hash':source_hash(source),'target':target})
    for previous in sorted((root/'artifacts').glob('bridge-probe-*'),reverse=True):
        old=previous/'Mods/ModLingo.Probe/ModLingo.Probe.dll'
        captured=previous/'Mods/ModLingo.Bridge/captures.json'
        if previous==profile or not old.exists() or not captured.exists():continue
        if old.read_bytes()==(fixture/'ModLingo.Probe.dll').read_bytes():
            for r in json.loads(captured.read_text(encoding='utf-8'))['captures']:
                if r['mod_id']=='ModLingo.Probe' and r['adapter']=='draw' and r['source']=='Hello drawing fixture':
                    fixture_records.append(dict(r,target='你好绘制测试'))
            break
    fixture_pack={'schema_version':1,'mod_id':'ModLingo.Probe','mod_version':'1.0.0','records':fixture_records}
    (translations/(digest('ModLingo.Probe')[:32]+'.json')).write_text(json.dumps(fixture_pack,ensure_ascii=False),encoding='utf-8')
    real_ids=('recon88.NoPamEnabling','7eventy7.StandardizedSeedSprites','jibb.minecarts')
    if args.real_mods_from:
        source_mods=Path(args.real_mods_from)
        for item in scan_mods(source_mods):
            if item.mod_id in real_ids:
                shutil.copytree(item.folder,mods/('Real-'+digest(item.mod_id)[:12]))
                dictionary=source_mods/'ModLingo.Bridge/translations'/(digest(item.mod_id)[:32]+'.json')
                if dictionary.exists():shutil.copy2(dictionary,translations/dictionary.name)
        (fixture/'real-menus.json').write_text(json.dumps(real_ids),encoding='utf-8')
    log=Path(os.environ['APPDATA'])/'StardewValley/ErrorLogs/SMAPI-latest.txt'
    original=log.read_bytes() if log.exists() else None
    if original is not None:(profile/'previous-smapi-log.txt').write_bytes(original)
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    with (profile/'stdout.txt').open('wb') as output:
        process=subprocess.Popen([str(game/'StardewModdingAPI.exe'),'--mods-path',str(mods),'--no-terminal'],cwd=str(game),stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            process.terminate();process.wait(timeout=10)
    if log.exists():
        current=log.read_bytes();(profile/'smapi-log.txt').write_bytes(current)
        if original is not None and b'ModLingo.Probe' in current:
            try:
                check_game_stopped()
                log.write_bytes(original)
            except ValueError:
                pass
    result=json.loads((fixture/'probe-result.json').read_text(encoding='utf-8')) if (fixture/'probe-result.json').exists() else {'error':'probe did not complete'}
    data=json.loads((bridge/'captures.json').read_text(encoding='utf-8-sig')) if (bridge/'captures.json').exists() else {'captures':[]}
    result.update({'capture_adapters':sorted(set(x['adapter'] for x in data['captures'])),'applied':data.get('applied',{}),'captures':len(data['captures']),'exit_code':process.returncode,'profile':str(profile),'paid_api_calls':0,'save_loaded':False})
    result['private_input_ignored']=not any(x.get('source')=='private input sentinel' for x in data['captures'])
    result['gmcm_capabilities']=data.get('gmcm_capabilities',{})
    result['unique_hits_checked']=len(data.get('applied_unique',[]))==sum(data.get('applied',{}).values()) and bool(data.get('applied_unique'))
    real_result=fixture/'real-menu-results.json'
    if real_result.exists():result['real_menu_results']=json.loads(real_result.read_text(encoding='utf-8'))
    result['language_scope_extraction_checked']=language_extraction_checked
    mixed=next((c for c in data['captures'] if c.get('mod_id')=='ModLingo.ProbeCP' and c.get('asset')=='Data/Events/Town' and c.get('path')==['ModLingoProbeMixedClosing']),None)
    result['closing_resolved_capture_checked']=bool(mixed and mixed['source']=='pause 1/message "Localized opening fixture"/end dialogue Abigail "Literal closing fixture"')
    captured_bundle=extract(cp_item,[mixed]) if mixed else None
    result['closing_resolved_extraction_checked']=bool(captured_bundle and any(r.captured and any(p.role=='end-dialogue' and p.text=='Literal closing fixture' for p in r.parts) for r in captured_bundle.records))
    result['mixed_capture_status_checked']=bool(captured_bundle and captured_bundle.category=='部分支持' and any(p['path']==['ModLingoProbeMixedClosing'] and p['observed_path'] for p in captured_bundle.pending_captures))
    result['world_map_capture_checked']=any(c.get('mod_id')=='ModLingo.ProbeCP' and c.get('asset')=='Data/WorldMap' and c.get('path')==['ModLingoProbeRegion','MapAreas','area','Tooltips','tip','Text'] and c.get('source')=='Nested map tooltip' for c in data['captures'])
    result['numeric_capture_deduplicated']=sum(x.get('context')=='gmcm/AddNumberOption/formatValue/int/probe-days' for x in data['captures'])==1
    result['target_field_capture_checked']=any(x.get('mod_id')=='ModLingo.ProbeCP' and x.get('path')==['ModLingoProbeObject','DisplayName'] and x.get('source')=='Nested flower name' for x in data['captures'])
    result['early_literal_captured']=any(x.get('context')=='gmcm/deferred-literal' and x.get('source')=='Early registered literal' for x in data['captures'])
    result['gmcm_existing_context_preserved']=sum(x.get('source')=='Hello GMCM fixture' for x in data['captures'])==1 and any(x.get('source')=='Hello GMCM fixture' and x.get('context')=='gmcm/AddParagraph/text' for x in data['captures'])
    result['gmcm_existing_context_preserved'] &= sum(x.get('source')=='Untranslated paragraph fixture' for x in data['captures'])==1 and any(x.get('source')=='Untranslated paragraph fixture' and x.get('context')=='gmcm/AddParagraph/text' for x in data['captures'])
    runtime_keys=('startup_capture_checked','startup_no_calls_checked','unique_hits_checked','gmcm_runtime_text_checked','gmcm_runtime_deferred_checked','gmcm_runtime_null_checked','gmcm_runtime_page_checked','gmcm_runtime_raw_checked','gmcm_runtime_cache_checked','gmcm_existing_context_preserved')
    (root/('artifacts/game-probe-'+__version__+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key!='real_menu_results'},ensure_ascii=True))
    if result.get('error') or not all(result.get(key) for key in (*runtime_keys,'world_map_checked','world_map_duplicate_guard_checked','world_map_capture_checked','mixed_capture_status_checked','closing_resolved_capture_checked','closing_resolved_extraction_checked','closing_resource_checked','closing_arguments_checked','header_resource_checked','asset_checked','language_scope_checked','language_scope_extraction_checked','bubble_resource_checked','bubble_command_checked','event_resource_checked','target_field_checked','target_field_capture_checked','gmcm_name_checked','gmcm_builtin_name_checked','gmcm_choice_checked','gmcm_anonymous_choice_checked','gmcm_numeric_checked','numeric_capture_deduplicated','early_literal_captured','raw_choice_unchanged','private_input_ignored')) or not {'gmcm','draw','asset'}.issubset(result['capture_adapters']):
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
