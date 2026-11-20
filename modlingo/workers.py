"""Spawn-safe CPU work. IPC contains small paths rather than whole projects."""
from dataclasses import asdict
import json
from pathlib import Path
from .cache import Cache, digest
from .core import Project, Entry, ProjectError, read_jsonc
from .extraction import extract, Bundle, Record, TextUnit
from .syntax import Span

def prepare_spool(item, storage, spool, captures):
    bridge_mods=captures if isinstance(captures,(str,Path)) else None
    if isinstance(captures,(str,Path)):
        from .bridge_io import captures as load_captures
        captures = load_captures(captures)
    storage = Path(storage)
    identity = digest(str(Path(item.folder).resolve()).casefold() + '\0' + item.mod_id.casefold())
    # Historical filenames used SHA over the raw identity, not JSON encoding.
    import hashlib
    old_id = hashlib.sha256((str(Path(item.folder).resolve()).casefold()+'\0'+item.mod_id.casefold()).encode()).hexdigest()[:24]
    old = storage / 'batch-projects' / (old_id + '.json')
    migration_note = ''
    if old.exists():
        cache = Cache(storage)
        try:
            cache.migrate(old)
        except (ProjectError, OSError, ValueError):
            migration_note = '旧缓存异常，原文件已保留；本次仅使用通过校验的 SQLite 结果'
        finally:
            cache.close()
    bundle = extract(item, captures)
    snapshot=None
    if bridge_mods is not None:
        from .bridge_io import dictionary_snapshot
        from .syntax import validate_rebuilt
        snapshot,previous=dictionary_snapshot(bridge_mods,item)
        candidates={(r.get('context'),r.get('source_hash')):r for r in previous if isinstance(r,dict)}
        display_candidates={}
        for row in previous:
            if not isinstance(row,dict) or row.get('adapter')!='gmcm' or not isinstance(row.get('source'),str) or not isinstance(row.get('target'),str):continue
            for role in ('/formatAllowedValue','/name','/tooltip','/text'):
                if row.get('context','').endswith(role) and row['target'].strip() and row['target']!=row['source']:
                    display_candidates.setdefault((row['source'],role),set()).add(row['target'])
        from .extraction import source_hash
        for record in bundle.records:
            old=candidates.get((record.context,source_hash(record.source)))
            if record.adapter!='i18n' and old and old.get('source')==record.source and isinstance(old.get('target'),str) and old['target'].strip() and validate_rebuilt(record.source,old['target'],record.kind):
                record.target=old['target']
            elif record.origin.startswith('cp-config/') and not record.target:
                # Reuse observed display translations only in the same pack and
                # display role. Conflicting translations deliberately disable reuse.
                role='/formatAllowedValue' if record.origin.startswith('cp-config/value/') else '/name' if record.origin=='cp-config/name' else '/tooltip' if record.origin=='cp-config/description' else '/text'
                targets={target for target in display_candidates.get((record.source,role),())
                         if validate_rebuilt(record.source,target,record.kind)}
                if len(targets)==1:record.target=targets.pop()
    if migration_note:
        bundle.notes.append(migration_note)
    path = Path(spool)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as out:
        meta = {'category': bundle.category, 'notes': bundle.notes, 'project': None,'bridge_snapshot':snapshot,'excluded_patches':bundle.excluded_patches,'pending_captures':bundle.pending_captures,
                'source_snapshots':bundle.source_snapshots}
        if bundle.project:
            project = bundle.project
            meta['project'] = {'mod_id': project.mod_id, 'name': project.name, 'folder': project.folder, 'version':project.version,
                               'language_layout':project.language_layout, 'external_chinese_keys':project.external_chinese_keys}
        out.write(json.dumps(meta, ensure_ascii=False)+'\n')
        for record in bundle.records:
            out.write(json.dumps({'record':asdict(record)},ensure_ascii=False)+'\n')
        for unit in bundle.units:
            out.write(json.dumps({'unit':asdict(unit)},ensure_ascii=False)+'\n')
    return str(path)

def read_spool(item, path):
    with Path(path).open(encoding='utf-8') as stream:
        meta = json.loads(next(stream))
        bundle = Bundle(item, category=meta['category'], notes=meta['notes'])
        bundle.excluded_patches=meta.get('excluded_patches',[])
        bundle.pending_captures=meta.get('pending_captures',[])
        bundle.bridge_snapshot=meta.get('bridge_snapshot')
        bundle.source_snapshots=meta.get('source_snapshots',{})
        if meta['project']:
            bundle.project = Project(**meta['project'])
        for line in stream:
            row = json.loads(line)
            if 'record' in row:
                data = row['record']
                data['parts'] = [Span(**p) for p in data['parts']]
                record = Record(**data)
                bundle.records.append(record)
                if record.adapter == 'i18n':
                    bundle.project.entries[record.context] = Entry(record.source,record.target,'imported' if record.target else '',record.source if record.target else '')
            else:
                bundle.units.append(TextUnit(**row['unit']))
    return bundle

def scan_worker(game):
    from .discovery import scan_mods
    from .extraction import scan_coverage
    from .bridge_io import captures
    items = [item for item in scan_mods(Path(game)/'Mods') if item.mod_id != 'ModLingo.Bridge']
    coverage = scan_coverage(items,captures(Path(game)/'Mods'))
    for item, row in zip(items, coverage):
        if row['category'] != '文件异常':
            item.reason = ''
        item.category = row['category']
        item.coverage = row
    return items

def detect_worker(saved):
    from .discovery import discover_games
    games = discover_games(saved)
    selected = games[0] if len(games)==1 else next((g for g in games if str(g)==saved),None)
    return games, selected, scan_worker(selected) if selected else []

def call_in_process(function, *args):
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn')) as pool:
        return pool.submit(function, *args).result()

def _spool_loop(requests,responses):
    while True:
        args=requests.get()
        if args is None:
            return
        try:
            responses.put((True,prepare_spool(*args)))
        except Exception as exc:
            responses.put((False,str(exc) if isinstance(exc,(ValueError,OSError)) else '扫描/迁移进程遇到异常'))

class SpoolWorker:
    def __init__(self):
        import multiprocessing
        context=multiprocessing.get_context('spawn')
        self.requests,self.responses=context.Queue(1),context.Queue(1)
        self.process=context.Process(target=_spool_loop,args=(self.requests,self.responses),daemon=True)
        self.process.start()

    async def prepare(self,args,stop):
        import asyncio,queue
        self.requests.put(args)
        while self.process.is_alive():
            if stop.is_set():
                self.process.terminate()
                self.process.join(timeout=2)
                raise InterruptedError('用户停止扫描，已取消准备进程')
            try:
                success,result=self.responses.get_nowait()
                if not success:
                    raise ValueError(result)
                return result
            except queue.Empty:
                await asyncio.sleep(.05)
        raise OSError('准备进程已退出')

    def close(self):
        if self.process.is_alive():
            self.requests.put(None)
            self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=2)
        for q in (self.requests,self.responses):
            q.cancel_join_thread()
            q.close()
