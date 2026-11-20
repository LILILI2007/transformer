"""Read-only, bounded adapters and a common text-unit contract."""
from dataclasses import dataclass, field, asdict
import hashlib
import json
import re
from pathlib import Path
from .core import Project, Entry, ProjectError, read_jsonc, locale_paths, merge_language_files, TOKEN
from .syntax import spans, Span,kind_for
from .discovery import linked, within

VISIBLE_FIELDS = {'DisplayName', 'Description', 'Text', 'Title', 'Dialogue', 'Question', 'Objective', 'CompletionMessage', 'DisplayDescription', 'DisplayTitle'}
TEXT_ASSETS = ('Characters/Dialogue/', 'Strings/', 'Data/mail', 'Data/Events/', 'Data/Quests', 'Data/Objects', 'Data/BigCraftables', 'Data/Weapons', 'Data/Furniture', 'Data/Buildings', 'Data/Characters', 'Data/SpecialOrders', 'Data/Movies', 'Data/Festivals', 'Data/NPCGiftTastes', 'Data/EngagementDialogue', 'Data/ExtraDialogue', 'Data/SecretNotes')
KNOWN_LANGUAGES = frozenset(('de','en','es','fr','hu','it','ja','ko','pt','ru','tr','zh'))
TEXT_ASSETS=TEXT_ASSETS+('Data/WorldMap',)

def world_map_path(pointer,strict_ids=True):
    if len(pointer)==4 and str(pointer[1]).casefold()=='mapareas' and str(pointer[3]).casefold()=='scrolltext':
        path=[pointer[0],'MapAreas',pointer[2],'ScrollText'];ids=path[::2]
    elif len(pointer)==6 and str(pointer[1]).casefold()=='mapareas' and str(pointer[3]).casefold()=='tooltips' and str(pointer[5]).casefold()=='text':
        path=[pointer[0],'MapAreas',pointer[2],'Tooltips',pointer[4],'Text'];ids=path[::2]
    else:return None
    return path if not strict_ids or all(isinstance(key,str) and re.fullmatch(r'[A-Za-z0-9_.-]+',key) for key in ids) else None

def unrelated_language(change):
    """Only literal, known foreign languages. Never evaluate arbitrary CP tokens."""
    if change.get('Action') not in ('Include','EditData','Load','EditImage','EditMap'):
        return ''
    when=change.get('When')
    language=when.get('Language') if isinstance(when,dict) else None
    if isinstance(language,str):
        values=[value.strip() for value in language.split(',')]
        if values and all(value in KNOWN_LANGUAGES for value in values) and not set(values)&{'en','zh'}:
            return 'When.Language='+','.join(values)
    locale=change.get('TargetLocale')
    if change.get('Action')=='EditData' and isinstance(locale,str) and locale in KNOWN_LANGUAGES-{'en','zh'}:
        return 'TargetLocale='+locale
    return ''

def canonical_asset(asset):
    for prefix in TEXT_ASSETS:
        if asset.casefold().startswith(prefix.casefold()):
            return prefix + asset[len(prefix):]
    return asset

@dataclass
class TextUnit:
    id: str
    mod_id: str
    origin: str
    context: str
    source: str
    record: int
    fragment: int
    adapter: str

@dataclass
class Record:
    context: str
    source: str
    kind: str
    adapter: str
    origin: str
    parts: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    asset: str = ''
    path: list = field(default_factory=list)
    target: str = ''
    captured: bool = False

@dataclass
class Bundle:
    item: object
    project: object = None
    records: list = field(default_factory=list)
    units: list = field(default_factory=list)
    category: str = '待确认'
    notes: list = field(default_factory=list)
    source_files: list = field(default_factory=list)
    source_snapshots: dict = field(default_factory=dict)
    excluded_patches: list = field(default_factory=list)
    pending_captures: list = field(default_factory=list)

def source_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def validate_sources(bundle):
    for relative,expected in bundle.source_snapshots.items():
        path=_safe(bundle.item.folder,Path(bundle.item.folder)/relative)
        if not path.is_file():
            raise ProjectError('提取后资源文件删除：'+relative)
        if path.stat().st_size>20*1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ProjectError('提取后资源文件修改：'+relative+'；请重新扫描')

def event_kind(text, context=''):
    if context == 'Data/Quests':
        return 'quest'
    if context == 'Data/NPCGiftTastes':
        return 'gift'
    return 'event' if 'Data/Events/' in context else kind_for(text)

def _safe(folder, path):
    path = Path(path)
    if not within(path, folder):
        raise ProjectError('引用文件不在 mod 目录内')
    current = path
    while current != Path(folder):
        if linked(current):
            raise ProjectError('引用文件含链接或目录联接')
        current = current.parent
    return path

def _add(bundle, record):
    parts, notes = spans(record.source, record.kind)
    record.parts, record.notes = parts, notes
    index = len(bundle.records)
    bundle.records.append(record)
    closing_count=0
    for n, part in enumerate(parts):
        closing=part.role=='end-dialogue'
        cache_index=closing_count if closing else n-closing_count
        if closing:closing_count+=1
        if any(c.isalpha() for c in part.text):
            namespace='/end-dialogue/' if closing else '/bubble/' if part.role=='bubble' else '/span/'
            bundle.units.append(TextUnit(str(index)+':'+str(n), bundle.item.mod_id, record.origin, record.context + namespace + str(cache_index), part.text, index, n, record.adapter))

def extract(item, bridge_captures=None):
    bundle, folder = Bundle(item), Path(item.folder)
    bundle.source_files.append('manifest.json')
    manifest = read_jsonc(folder / 'manifest.json')
    if not isinstance(manifest, dict) or not manifest.get('UniqueID'):
        raise ProjectError('manifest.json 缺少有效 UniqueID')
    if locale_paths(folder, 'default'):
        bundle.source_files.extend(str(path.relative_to(folder)) for path in locale_paths(folder,'default'))
        try:
            project = Project.from_mod(folder)
            bundle.project = project
            for key, entry in project.entries.items():
                record = Record(key, entry.source, event_kind(entry.source), 'i18n', 'i18n', target=entry.target)
                _add(bundle, record)
            bundle.category = '可直接翻译'
        except (ProjectError,OSError,ValueError) as exc:
            bundle.notes.append('i18n 文件异常，原文件未改写；仅处理独立资源/插件采集：'+str(exc))
            bundle.category='文件异常'
    pack = manifest.get('ContentPackFor') or {}
    if not isinstance(pack, dict):
        raise ProjectError('ContentPackFor 必须是对象')
    is_cp = str(pack.get('UniqueID', '')).casefold() == 'pathoschild.contentpatcher'
    visited, visual_only, unknown = set(), True, []
    has_config=False
    def config_schema(data):
        """CP's native config.* keys change labels only, never config values."""
        nonlocal has_config
        schema=data.get('ConfigSchema')
        if not isinstance(schema,dict):return
        has_config=True
        try:
            chinese=merge_language_files(locale_paths(folder,'zh'))
        except (ProjectError,OSError,ValueError) as exc:
            unknown.append('配置显示译文未导入：'+str(exc));return
        if bundle.category=='文件异常':return
        if bundle.project is None:
            bundle.project=Project(item.mod_id,item.name,str(folder),item.version)
            if (folder/'i18n/default').is_dir():bundle.project.language_layout='split'
        project=bundle.project
        existing={key.casefold() for key in project.entries}
        zh={key.casefold():value for key,value in chinese.items() if isinstance(value,str)}
        from .compatibility import display_target
        def add(key,source,origin,legacy_context):
            if not isinstance(source,str) or not source.strip() or '{{' in source:return
            if key.casefold() in existing:
                # Existing default keys remain authoritative; enrich only an
                # exact-source display field, and preserve real Chinese edits.
                for record in bundle.records:
                    if record.adapter=='i18n' and record.context.casefold()==key.casefold() and record.source==source:
                        record.origin=origin
                        if not record.target or record.target==source:
                            record.target=display_target(item.mod_id,legacy_context,source)
                return
            existing.add(key.casefold())
            target=zh.get(key.casefold(),'') or display_target(item.mod_id,legacy_context,source)
            project.entries[key]=Entry(source,target,'imported' if target else '',source if target else '')
            _add(bundle,Record(key,source,'text','i18n',origin,target=target))
        for name,field in schema.items():
            if not isinstance(field,dict) or not isinstance(name,str) or not name or '{{' in name:continue
            allowed=field.get('AllowValues','')
            values=[v.strip() for v in allowed.split(',') if v.strip()] if isinstance(allowed,str) else []
            boolean=set(v.casefold() for v in values)=={'true','false'} and not field.get('AllowMultiple',False)
            api='AddBoolOption' if boolean else 'AddTextOption'
            add('config.'+name+'.name',name,'cp-config/name','gmcm/'+api+'/name')
            add('config.'+name+'.description',field.get('Description'),'cp-config/description','gmcm/'+api+'/tooltip')
            section=field.get('Section')
            if isinstance(section,str):add('config.section.'+section.strip()+'.name',section.strip(),'cp-config/section','gmcm/AddSectionTitle/text')
            if not boolean:
                scope='cp-config/value/'+source_hash(json.dumps(values,ensure_ascii=False))
                for value in values:
                    if re.search('[A-Za-z]',value) and '{{' not in value:
                        add('config.'+name+'.values.'+value,value,scope,'gmcm/AddTextOption/formatAllowedValue')
    def read_changes(path, inherited=False, target=None):
        nonlocal visual_only
        path = _safe(folder, path)
        relative=str(path.relative_to(folder))
        if relative not in bundle.source_files:bundle.source_files.append(relative)
        if path in visited:
            return
        if len(visited) >= 512:
            raise ProjectError('CP 引用文件超过 512 个')
        visited.add(path)
        try:
            if path.stat().st_size>20*1024*1024:
                raise ProjectError('单个文件超过 20 MB')
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            data = read_jsonc(path, numeric_properties=True)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=before:
                raise ProjectError('提取期间资源文件发生变化，请重新扫描')
            bundle.source_snapshots[relative]=before
        except (ProjectError, OSError) as exc:
            visual_only = False
            unknown.append('引用文件未解析，保留原文件：' + str(path.relative_to(folder)) + ' · ' + str(exc))
            return
        if target is not None:
            walk_values(data, target, [], str(path.relative_to(folder)))
            return
        if isinstance(data,dict) and data.get('ConfigSchema'):
            visual_only=False
            if path==folder/'content.json':config_schema(data)
            else:unknown.append('非根文件 ConfigSchema 需游戏确认')
        changes = data.get('Changes', []) if isinstance(data, dict) else data
        if not isinstance(changes, list):
            unknown.append('未知 CP 文件结构')
            return
        for n, change in enumerate(changes):
            if not isinstance(change, dict):
                continue
            action = str(change.get('Action', ''))
            targets = str(change.get('Target', '')).split(',')
            origin = str(path.relative_to(folder)) + '/Changes/' + str(n)
            exclusion=unrelated_language(change)
            if exclusion:
                bundle.excluded_patches.append({'origin':origin,'reason':'仅其他语言补丁；保留原文件，不派发翻译','condition':exclusion})
                continue
            if action == 'Include':
                for name in str(change.get('FromFile', '')).split(','):
                    name = name.strip()
                    if not name or '{{' in name or '*' in name:
                        unknown.append('动态 Include 需游戏采集')
                    else:
                        read_changes(folder / name, True)
            elif action in ('EditData', 'Load'):
                for asset in targets:
                    asset = canonical_asset(asset.strip().replace('\\', '/'))
                    if asset.startswith('Data/WorldMap') and asset!='Data/WorldMap':
                        unknown.append('未识别数据资源需游戏采集：'+asset)
                        continue
                    if asset == 'spacechase0.SpaceCore/TextureOverrides' or asset == 'Data/ChairTiles':
                        continue
                    if action=='Load' and not asset.startswith(TEXT_ASSETS) and str(change.get('FromFile','')).lower().endswith(('.png','.jpg','.jpeg','.webp','.bmp')):
                        # Known image files have no extractable text. This is
                        # excluded image/OCR work, not an unsupported text mod.
                        continue
                    if '{{' in asset or not asset.startswith(TEXT_ASSETS):
                        unknown.append('未识别数据资源需游戏采集：' + asset)
                        continue
                    visual_only = False
                    scope=change.get('TargetField',[])
                    if not isinstance(scope,list) or len(scope)>12 or any(type(key) not in (str,int) or (isinstance(key,str) and ('{{' in key or '\x1f' in key)) for key in scope):
                        unknown.append('动态或无效 TargetField 需游戏采集：'+asset)
                        continue
                    scope=[str(key) for key in scope]
                    if action == 'Load':
                        name = str(change.get('FromFile', ''))
                        if name.endswith('.json') and '{{' not in name:
                            read_changes(folder / name, target=asset)
                        else:
                            unknown.append('动态或 XNB Load 需游戏采集')
                    else:
                        walk_values(change.get('Entries', {}), asset, list(scope), origin)
                        fields = change.get('Fields', {})
                        if isinstance(fields, dict):
                            for entry, values in fields.items():
                                walk_values(values, asset, scope+[str(entry)], origin)
                        if change.get('TextOperations'):
                            unknown.append('TextOperations 按游戏最终资源采集')
            elif action not in ('EditImage', 'EditMap'):
                unknown.append('未识别补丁动作：' + action)
    def walk_values(value, asset, pointer, origin):
        if isinstance(value, dict):
            for key, child in value.items():
                walk_values(child, asset, pointer + [key], origin)
        elif isinstance(value, list):
            if asset=='Data/WorldMap':
                location=[str(key).casefold() for key in pointer]
                if not (len(location)==2 and location[1]=='mapareas' or len(location)==4 and location[1]=='mapareas' and location[3]=='tooltips'):
                    return
                ids=[next((v for k,v in child.items() if k.casefold()=='id'),None) if isinstance(child,dict) else None for child in value]
                if not all(isinstance(key,str) and re.fullmatch(r'[A-Za-z0-9_.-]+',key) for key in ids) or len(set(ids))!=len(ids):
                    unknown.append('地图列表缺少唯一静态 Id，保留原文待游戏采集：'+asset+'/'+'/'.join(pointer))
                    return
                for key,child in zip(ids,value):walk_values(child,asset,pointer+[key],origin)
                return
            for n, child in enumerate(value):
                walk_values(child, asset, pointer + [str(n)], origin)
        elif isinstance(value, str):
            if asset=='Data/WorldMap':
                normalized=world_map_path(pointer)
                if normalized is None:
                    if world_map_path(pointer,False):unknown.append('地图动态或序号路径尚未支持，保留原文：'+asset+'/'+'/'.join(pointer))
                    return
                pointer=normalized
                if '{{' in value and '{{i18n:' not in value:
                    unknown.append('地图动态文本需游戏解析后采集：'+asset+'/'+'/'.join(pointer))
                    return
            simple = asset.startswith(('Characters/Dialogue/', 'Strings/', 'Data/mail', 'Data/Events/', 'Data/Quests', 'Data/NPCGiftTastes', 'Data/EngagementDialogue', 'Data/ExtraDialogue', 'Data/SecretNotes'))
            allowed = VISIBLE_FIELDS | ({'Name'} if asset in ('Data/SpecialOrders','Data/Buildings') else set())
            if asset!='Data/WorldMap' and not simple and (not pointer or pointer[-1] not in allowed):
                return
            if '{{i18n:' in value:
                parts,_=spans(value,event_kind(value,asset))
                if any(re.search('[A-Za-z]',TOKEN.sub('',part.text)) for part in parts):
                    bundle.pending_captures.append({'origin':origin,'asset':asset,'path':list(pointer),
                        'context':asset+'/'+'/'.join(pointer),'observed_path':False,
                        'reason':'混合 i18n 引用与硬编码文字，需游戏解析后采集，不能静态改写',
                        'hint':'启用 Bridge，进入相关地图或界面使资源加载；退出游戏后重新扫描并翻译，其他条件版本仍待验收'})
                return
            context = asset + '/' + '/'.join(pointer)
            _add(bundle, Record(context, value, event_kind(value, asset), 'asset', origin, asset=asset, path=pointer))
    if is_cp and (folder / 'content.json').is_file():
        read_changes(folder / 'content.json')
        bundle.notes.extend(list(dict.fromkeys(unknown)))
        if bundle.units:
            bundle.category = '部分支持' if unknown else '可直接翻译'
        elif visual_only and not unknown:
            bundle.category = '仅其他语言' if bundle.excluded_patches else '无需翻译'
        else:
            bundle.category = '需进游戏采集'
    if manifest.get('EntryDll') and bundle.category in ('待确认','仅其他语言'):
        bundle.category = '需进游戏采集'
    if (manifest.get('EntryDll') or has_config) and isinstance(manifest.get('Name'),str):
        from .mod_names import display_alias
        _add(bundle,Record('gmcm/mod-name',manifest['Name'],'text','gmcm','manifest',target=display_alias(item.mod_id,manifest['Name'])))
        if bundle.category=='需进游戏采集':
            bundle.category='部分支持'
            bundle.notes.append('已提取 mod 显示名称；配置页文字仍需进入 GMCM 采集')
    if any(folder.rglob('*.xnb')):
        bundle.notes.append('XNB 资源类型与文本待游戏加载确认')
        if bundle.category in ('待确认','仅其他语言'):
            bundle.category = '需进游戏采集'
    observed_paths=set()
    if bridge_captures:
        seen={(record.context,record.source,record.adapter) for record in bundle.records}
        for capture in bridge_captures:
            if capture.get('mod_id') != item.mod_id or capture.get('mod_version') != item.version:
                continue
            source = capture.get('source', '')
            if not isinstance(source, str) or capture.get('source_hash') != source_hash(source):
                continue
            asset,path=capture.get('asset'),capture.get('path')
            if capture.get('adapter')=='asset' and isinstance(asset,str) and isinstance(path,list) and all(isinstance(key,str) for key in path) and '{{i18n:' not in source:
                observed_paths.add((asset.casefold(),tuple(path)))
            identity=(capture['context'],source,capture.get('adapter','draw'))
            if identity in seen:
                continue
            seen.add(identity)
            _add(bundle, Record(capture['context'], source, event_kind(source, capture.get('asset', '')), capture.get('adapter', 'draw'), 'bridge', asset=capture.get('asset',''), path=capture.get('path',[]), captured=True))
    if bundle.pending_captures:
        for pending in bundle.pending_captures:
            pending['observed_path']=(pending['asset'].casefold(),tuple(pending['path'])) in observed_paths
        observed=sum(p['observed_path'] for p in bundle.pending_captures)
        bundle.notes.append('混合 i18n 字段 %d 个需游戏解析采集/验收；%d 个资源位置已有采集，其他条件版本不能视为已覆盖。启用 Bridge 进入相关地图/界面，退出游戏后重新扫描。' % (len(bundle.pending_captures),observed))
        bundle.category='部分支持' if bundle.units else '需进游戏采集'
    if bundle.units and bundle.category in ('待确认', '需进游戏采集','仅其他语言'):
        bundle.category = '部分支持' if not bridge_captures else '可直接翻译'
    issues=list(dict.fromkeys(note for record in bundle.records for note in record.notes))
    if issues:
        bundle.notes.extend(issues[:12])
        if bundle.units:
            bundle.category='部分支持'
    if bundle.notes and bundle.units:bundle.category='部分支持'
    return bundle

def scan_coverage(items, bridge_captures=None):
    rows = []
    for item in items:
        try:
            bundle = extract(item,bridge_captures)
            rows.append({'mod_id': item.mod_id, 'name': item.name, 'category': bundle.category,
                         'text_units': len(bundle.units), 'records': len(bundle.records), 'notes': bundle.notes,
                         'excluded_patches':bundle.excluded_patches,
                         'pending_captures':bundle.pending_captures,
                         'in_game_verified': False})
        except (ProjectError, OSError, ValueError) as exc:
            rows.append({'mod_id': item.mod_id, 'name': item.name, 'category': '文件异常', 'text_units': 0, 'notes': [str(exc)], 'in_game_verified': False})
    return rows
