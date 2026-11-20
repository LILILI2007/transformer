"""Freeze only declared source JSON and offline captures; exclude paid targets."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from .bridge_io import captures,BRIDGE_ID
from .core import ProjectError,atomic_write
from .discovery import scan_mods,linked,within
from .extraction import extract

def freeze_sources(mods,destination):
    mods=Path(mods).resolve();destination=Path(destination).resolve()
    if destination.exists():raise ProjectError('原文快照已存在，不自动覆盖')
    if within(destination,mods):raise ProjectError('测试快照不能写入玩家 Mods')
    destination.mkdir(parents=True)
    offline=captures(mods)
    inventory=[];checksums={}
    for number,item in enumerate(scan_mods(mods)):
        if item.mod_id==BRIDGE_ID:continue
        row={'name':item.name,'mod_id':item.mod_id,'version':item.version,'source_folder':str(item.folder),
             'fixture_folder':str(number),'notes':[],'game_verified':False}
        files=['manifest.json']
        try:
            bundle=extract(item,offline);files=bundle.source_files
            row.update(category=bundle.category,records=len(bundle.records),fragments=len(bundle.units),notes=bundle.notes)
        except (ProjectError,OSError,ValueError) as exc:
            row.update(category='文件异常',records=0,fragments=0,notes=[str(exc)])
        for relative in sorted(set(files)):
            source=item.folder/relative
            if not within(source,item.folder) or linked(source) or linked(source.parent):raise ProjectError('快照来源路径不安全')
            if not source.is_file():continue
            # Source tracker never includes installed zh files / caches / config.
            target=destination/'Mods'/str(number)/relative;target.parent.mkdir(parents=True,exist_ok=True)
            data=source.read_bytes();target.write_bytes(data)
            checksums[str(target.relative_to(destination))]=hashlib.sha256(data).hexdigest()
        if any(item.folder.rglob('*.xnb')):
            # Presence marker only; never copy or edit original binary resources.
            marker=destination/'Mods'/str(number)/'source-resource-presence.xnb'
            marker.write_bytes(b'')
            checksums[str(marker.relative_to(destination))]=hashlib.sha256(b'').hexdigest()
        inventory.append(row)
    bridge=destination/'Mods'/BRIDGE_ID;bridge.mkdir(parents=True)
    if offline:
        raw=json.dumps({'schema_version':1,'captures':offline},ensure_ascii=False).encode('utf-8')
        (bridge/'captures.json').write_bytes(raw)
        checksums[str((bridge/'captures.json').relative_to(destination))]=hashlib.sha256(raw).hexdigest()
    report={'schema_version':1,'source_mods':str(mods),'inventory':inventory,'checksums':checksums,
            'contains_existing_translations':False,'contains_paid_cache':False,'captures':len(offline),
            'note':'仅复制作者声明的原文 JSON；XNB 仅存在性标记，不包含游戏或mod二进制。'}
    atomic_write(destination/'inventory.json',json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
    return report

def verify_snapshot(destination):
    destination=Path(destination);report=json.loads((destination/'inventory.json').read_text(encoding='utf-8'))
    for relative,expected in report['checksums'].items():
        path=destination/relative
        if not within(path,destination) or linked(path) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ProjectError('原文快照校验失败，不进行付费测试')
    # Unexpected installed translations/caches must not silently warm a cold run.
    if any((destination/'Mods').rglob('zh.json')) or any((destination/'Mods').rglob('translations')) or any(destination.rglob('*.sqlite3')):
        raise ProjectError('冷缓存快照包含译文或数据库，拒绝验收')
    return report
