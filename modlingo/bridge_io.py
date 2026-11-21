"""Versioned, offline exchange with the companion SMAPI mod."""
import json
from pathlib import Path
import shutil
import sys
from .core import ProjectError, atomic_write, read_jsonc
from .discovery import linked, within
from .extraction import source_hash
from .cache import digest

BRIDGE_ID = 'ModLingo.Bridge'

def bridge_directory(mods):
    path = Path(mods) / BRIDGE_ID
    if linked(path):
        raise ProjectError('插件目录不能是链接')
    return path

def plugin_status(mods):
    """Disk installation only: never claim that SMAPI loaded the plugin."""
    try:
        target=bridge_directory(mods)
        manifest=target/'manifest.json';dll=target/'ModLingo.Bridge.dll'
        if any(linked(path) for path in (manifest,dll)):raise ProjectError('插件文件不能是链接')
        if not manifest.is_file() or not dll.is_file() or dll.stat().st_size==0:
            return {'ready':False,'reason':'伴随插件缺失或不完整；插件译文需要安装完整程序','version':''}
        data=read_jsonc(manifest)
        if data.get('UniqueID')!=BRIDGE_ID or data.get('EntryDll')!='ModLingo.Bridge.dll':
            raise ProjectError('插件身份或入口不符')
        return {'ready':True,'reason':'插件程序已安装；SMAPI 实际加载及游戏效果待确认','version':str(data.get('Version',''))}
    except (ProjectError,OSError,ValueError) as exc:
        return {'ready':False,'reason':str(exc),'version':''}

def captures(mods):
    path = bridge_directory(mods) / 'captures.json'
    if not path.exists():
        return []
    if linked(path):
        raise ProjectError('采集文件不能是链接')
    data = read_jsonc(path)
    if data.get('schema_version') != 1 or not isinstance(data.get('captures'), list):
        raise ProjectError('采集文件版本或格式不兼容')
    valid = []
    for record in data['captures'][:20000]:
        if not isinstance(record,dict):
            continue
        if all(isinstance(record.get(k),str) for k in ('mod_id','mod_version','context','source','source_hash')) and isinstance(record.get('path',[]),list) and all(isinstance(k,str) for k in record.get('path',[])):
            valid.append(record)
    return valid


def runtime_evidence(mods):
    """Evidence from one recorded game session; never a full-coverage claim."""
    path=bridge_directory(mods)/'captures.json'
    try:
        if not path.is_file() or linked(path):return {}
        data=read_jsonc(path)
        if data.get('bridge_version')!=plugin_status(mods).get('version'):return {}
        return {key:data.get(key) for key in ('session_id','bridge_version','extraction_rules_version','extraction_rules_hash','gmcm_version','gmcm_capabilities','applied_unique')}
    except (ProjectError,OSError,ValueError):return {}

def install_plugin(mods, source=None):
    from .batch import check_game_stopped
    check_game_stopped()
    source = Path(source or Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))/'bridge'/'package')
    if not (source/'ModLingo.Bridge.dll').is_file():
        raise ProjectError('便携包中缺少伴随插件，请重新下载完整包')
    target = bridge_directory(mods)
    target.mkdir(exist_ok=True)
    if (target/'manifest.json').exists() and read_jsonc(target/'manifest.json').get('UniqueID') != BRIDGE_ID:
        raise ProjectError('目标文件夹属于其他 mod，未覆盖')
    for name in ('manifest.json','ModLingo.Bridge.dll'):
        if linked(target/name):
            raise ProjectError('插件文件不能是链接')
        atomic_write(target/name,(source/name).read_bytes())
    for name in ('names.zh.json','display.zh.json'):
        if (source/name).is_file():
            if linked(target/name):raise ProjectError('插件名称文件不能是链接')
            atomic_write(target/name,(source/name).read_bytes())
    return target

def ensure_plugin(mods,source=None):
    source=Path(source or Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))/'bridge'/'package')
    status=plugin_status(mods)
    wanted=read_jsonc(source/'manifest.json')
    import re
    def version(text):
        match=re.match(r'^(\d+)\.(\d+)\.(\d+)',text)
        return tuple(map(int,match.groups())) if match else (0,0,0)
    if not status['ready'] or version(status['version'])<version(str(wanted.get('Version',''))):
        install_plugin(mods,source)
    return plugin_status(mods)

def uninstall_plugin(mods):
    from .batch import check_game_stopped
    check_game_stopped()
    target = bridge_directory(mods)
    if not target.exists():
        return
    if read_jsonc(target/'manifest.json').get('UniqueID') != BRIDGE_ID:
        raise ProjectError('插件身份不符，拒绝卸载')
    for name in ('manifest.json','ModLingo.Bridge.dll','names.zh.json','display.zh.json'):
        path=target/name
        if path.exists() and not linked(path):
            path.unlink()
    # Keep captured text and dictionaries for explicit reuse/recovery.

def dictionary_snapshot(mods, item):
    directory=bridge_directory(mods)/'translations'
    path=directory/(digest(item.mod_id)[:32]+'.json')
    if linked(directory) or linked(path):
        raise ProjectError('译文路径不能是链接')
    raw=path.read_bytes() if path.exists() else None
    data=read_jsonc(path) if raw is not None else {}
    if data and (data.get('schema_version')!=1 or data.get('mod_id')!=item.mod_id):
        raise ProjectError('译文目录身份不符')
    records=data.get('records',[]) if data.get('mod_version')==item.version else []
    return source_hash(raw.decode('utf-8')) if raw is not None else '',records


def install_dictionary(mods, item, records, receipt_dir, expected_snapshot=None):
    from .batch import check_game_stopped
    check_game_stopped()
    directory=bridge_directory(mods)/'translations'
    if linked(directory):
        raise ProjectError('译文目录不能是链接')
    directory.mkdir(parents=True,exist_ok=True)
    target=directory/(digest(item.mod_id)[:32]+'.json')
    if linked(target):
        raise ProjectError('译文文件不能是链接')
    before=target.read_bytes() if target.exists() else None
    if expected_snapshot is not None and (source_hash(before.decode('utf-8')) if before is not None else '') != expected_snapshot:
        raise ProjectError('扫描后插件译文发生变化，已保留玩家修改，请重新扫描')
    existing=read_jsonc(target) if target.exists() else {'schema_version':1,'mod_id':item.mod_id,'mod_version':item.version,'records':[]}
    if existing.get('mod_id') != item.mod_id or existing.get('schema_version') != 1:
        raise ProjectError('译文目录身份不符')
    previous = existing.get('records',[]) if existing.get('mod_version') == item.version else []
    merged={(r['context'],r['source_hash']):r for r in previous if isinstance(r,dict) and isinstance(r.get('context'),str) and isinstance(r.get('source_hash'),str)}
    for r in records:
        merged[(r['context'],r['source_hash'])]=r
    data={'schema_version':1,'mod_id':item.mod_id,'mod_version':item.version,'records':list(merged.values())}
    encoded=json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8')
    original=target.read_bytes() if target.exists() else None
    if original==encoded:
        return ''
    receipt_dir=Path(receipt_dir);receipt_dir.mkdir(parents=True,exist_ok=True)
    if original is not None:
        atomic_write(receipt_dir/'original-bridge.bin',original)
    receipt={'schema_version':2,'kind':'bridge','target':str(target),'mods':str(Path(mods).resolve()),'mod_id':item.mod_id,
             'installed_sha':source_hash(encoded.decode()),'original_sha':source_hash(original.decode()) if original is not None else '',
             'had_original':original is not None,'restored':False}
    path=receipt_dir/'receipt.json'
    atomic_write(path,json.dumps(receipt,ensure_ascii=False).encode('utf-8'))
    if (target.read_bytes() if target.exists() else None) != original:
        raise ProjectError('安装前插件译文再次变化，未覆盖')
    atomic_write(target,encoded)
    return str(path)

def restore_dictionary(receipt_path, data):
    from .batch import check_game_stopped
    check_game_stopped()
    target,mods=Path(data['target']),Path(data['mods'])
    expected=bridge_directory(mods)/'translations'/(digest(data['mod_id'])[:32]+'.json')
    if target != expected or not within(target,mods) or data.get('restored'):
        raise ProjectError('译文恢复路径或状态无效')
    current=target
    while current != mods:
        if linked(current):
            raise ProjectError('恢复路径含链接')
        current=current.parent
    if not target.exists() or source_hash(target.read_text(encoding='utf-8')) != data['installed_sha']:
        raise ProjectError('译文文件已有后续修改，未覆盖')
    if data['had_original']:
        original=(Path(receipt_path).parent/'original-bridge.bin').read_bytes()
        if source_hash(original.decode('utf-8')) != data['original_sha']:
            raise ProjectError('译文备份校验失败')
        atomic_write(target,original)
    else:
        target.unlink()
    data['restored']=True
    atomic_write(Path(receipt_path),json.dumps(data,ensure_ascii=False).encode('utf-8'))
