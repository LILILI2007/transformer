"""Fair concurrent scheduler; all mutations have one owner off the Tk thread."""
import asyncio
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import datetime
import json
import multiprocessing
import re
from pathlib import Path
import time
import uuid
from .async_provider import Session, ServiceError, translate_recover
from .batch import data_root, library_lock, check_game_stopped, install, validate_item, installation_conflicts
from .bridge_io import captures, install_dictionary,ensure_plugin,plugin_status,runtime_evidence
from .cache import Cache, digest
from .core import ProjectError, atomic_write, validate_translation, _unsupported_reason
from .provider import batches
from .syntax import rebuild, validate_rebuilt, spans,has_incremental_event_text
from .workers import prepare_spool, read_spool, SpoolWorker
from .extraction import source_hash, validate_sources
from .batching import fragment_groups, wire_batches, fits
from .adaptive import FairQueue
from .memory import memory_key,target_parts,already_chinese,whole_text
from .test_budget import BudgetStop
from .failures import Reason, reason, Circuits, CONTENT_CODES
from .compatibility import display_target,minecart_labels

class Engine:
    def __init__(self, items, mods, config, stop, storage=None, concurrency=4, notify=lambda kind,data:None, transport=None, use_processes=True, plan_decision=None, test_budget=None, retry_exhausted=False, offline_only=False):
        self.items,self.mods,self.config,self.stop=items,Path(mods).resolve(),replace(config),stop
        self.storage=Path(storage or data_root())
        self.concurrency,self.notify,self.transport,self.use_processes=concurrency,notify,transport,use_processes
        self.run_id=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        self.metrics={'phase':'准备任务','total':0,'completed':0,'failed':0,'cache_hits':0,'in_flight':0,'requests':0,'waiting_retry':0,'last_response':None,'eta_seconds':None,'concurrency':concurrency}
        self.started=time.monotonic()
        self.translation_started=None
        self.translation_paused=False
        self.answers,self.errors,self.bindings={}, {}, {}
        self.plan_decision=plan_decision
        self.retry_exhausted=retry_exhausted
        self.offline_only=offline_only
        self.test_budget=test_budget;self.budget_reason=''
        self.timings={'scan_prepare_seconds':0,'planning_seconds':0,'confirmation_wait_seconds':0,'network_seconds':0,'validation_seconds':0,'cache_write_seconds':0,'install_seconds':0}
        self.partial_report=None
        self.marker_cost=8 if config.provider=='deepseek' else 22
        self.metrics.update({'memory_hits':0,'duplicate_fragments':0,'preserved_chinese':0,'source_records':0})

    def emit(self):
        elapsed=time.monotonic()-self.translation_started if self.translation_started is not None else 0
        processed=self.metrics['completed']-self.metrics['cache_hits']+self.metrics['failed']
        remaining=max(0,self.metrics['total']-self.metrics['completed']-self.metrics['failed'])
        self.metrics['eta_seconds']=(round(remaining*elapsed/processed) if processed and elapsed>=5 else None) if remaining else 0
        if self.translation_started is None or self.translation_paused or self.stop.is_set():self.metrics['eta_seconds']=None
        self.notify('engine_progress',dict(self.metrics))

    def trace(self,row):
        try:
            path=self.storage/'reports'/(self.run_id+'.requests.jsonl')
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'time':datetime.now().astimezone().isoformat(timespec='seconds'),**row},ensure_ascii=False)+'\n')
        except OSError:
            self.metrics['diagnostic_error']='诊断记录保存失败；已完成译文仍写入 SQLite'

    async def heartbeat(self):
        while True:
            if self.test_budget:
                self.metrics['test_budget']=self.test_budget.snapshot()
                if self.test_budget.remaining()<=0:
                    self.budget_reason='达到测试时间/当日截止上限';self.stop.set()
            self.emit()
            await asyncio.sleep(1)

    async def run(self):
        check_game_stopped()
        with library_lock(self.mods,self.storage):
            beat=asyncio.create_task(self.heartbeat())
            try:
                return await self._run()
            except asyncio.CancelledError:
                return self.interrupted('测试已取消或达到总时限；已校验结果保存在缓存，未确认条目待重试','CancelledError')
            except Exception as exc:
                # _run closes its writer first. Preserve checkpoints and leave
                # uncertain work explicit; never automatically resubmit it.
                reason=str(exc) if isinstance(exc,BudgetStop) else '任务异常退出（%s）；已校验结果保存在缓存，未确认条目待手动重试' % type(exc).__name__
                return self.interrupted(reason,type(exc).__name__)
            finally:
                beat.cancel()
                await asyncio.gather(beat,return_exceptions=True)

    def interrupted(self,reason,kind):
        if self.test_budget:self.metrics['test_budget']=self.test_budget.snapshot()
        cache=Cache(self.storage)
        try:cache.mark_many(self.run_id,(key for key in self.bindings if key not in self.answers),'pending_retry',reason)
        finally:cache.close()
        self.translation_paused=True;self.metrics['phase']=reason;self.emit()
        self.trace({'event':'unexpected_failure','exception_type':kind})
        report=self.partial_report or {'schema_version':3,'run_id':self.run_id,'success':[],'failure':[],'verified_in_game':False}
        processed={row['folder'] for row in report['success']+report['failure']}
        report['failure'].extend(self.result(item,'已暂停',reason) for item in self.items if str(item.folder) not in processed)
        report.update(paused_reason=reason,metrics=dict(self.metrics),timings=self.timings,
                      counts=dict(Counter(row['status'] for row in report['success']+report['failure'])))
        path=self.storage/'reports'/(self.run_id+'.json')
        atomic_write(path,json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
        atomic_write(path.with_suffix('.txt'),reason.encode('utf-8'))
        return path,report

    async def _run(self):
        report={'schema_version':3,'run_id':self.run_id,'mods':str(self.mods),'target_language':'zh','success':[],'failure':[],'verified_in_game':False}
        self.partial_report=report
        self.runtime=runtime_evidence(self.mods)
        self.runtime_hits={tuple(hit.get(key) for key in ('mod_id','mod_version','context','source_hash','target_hash'))
                           for hit in self.runtime.get('applied_unique',[]) or [] if isinstance(hit,dict)}
        report['runtime_evidence']={key:value for key,value in self.runtime.items() if key!='applied_unique'}
        path=self.storage/'reports'/(self.run_id+'.json')
        acquired=str(self.mods)
        bundles=[]
        ids=Counter(item.mod_id.casefold() for item in self.items)
        pool=SpoolWorker() if self.use_processes else None
        try:
            for index,item in enumerate(self.items):
                if self.test_budget:self.test_budget.check()
                if self.stop.is_set():
                    break
                self.metrics['phase']='扫描/迁移缓存：%d/%d · %s' % (index+1,len(self.items),item.name)
                self.emit()
                try:
                    if not item.mod_id:
                        raise ProjectError(item.reason or 'manifest 缺少有效 UniqueID，无法确定文本归属')
                    if ids[item.mod_id.casefold()]>1:
                        raise ProjectError('重复 UniqueID，不能确定实际加载副本')
                    spool=self.storage/'tasks'/self.run_id/(str(index)+'.jsonl')
                    if pool:
                        result=await pool.prepare((item,self.storage,spool,acquired),self.stop)
                    else:
                        result=await asyncio.to_thread(prepare_spool,item,self.storage,spool,acquired)
                    bundles.append(read_spool(item,result))
                except (ProjectError,OSError,ValueError) as exc:
                    report['failure'].append(self.result(item,'已暂停' if self.stop.is_set() else '文件异常',str(exc)))
        finally:
            if pool:
                pool.close()
        self.timings['scan_prepare_seconds']=time.monotonic()-self.started
        planning_started=time.monotonic()
        cache=Cache(self.storage)
        pending_by_mod=[]
        wire_groups={}
        wire_contexts={}
        wire_markers={}
        wire_scopes={}
        circuits=Circuits()
        attempts=Counter()
        repair_failures={}
        whole_fields={}
        aliases={}
        memory_records={}
        try:
            for bundle in bundles:
                self.metrics['source_records']+=len(bundle.records)
                record_units={}
                for unit in bundle.units:record_units.setdefault(unit.record,[]).append(unit)
                pending=[]
                existing=set()
                recovered_parts={}
                previous_span_indices={}
                for index,record in enumerate(bundle.records):
                    if any(part.role=='bubble' for part in record.parts):
                        old_index=0
                        for n,part in enumerate(record.parts):
                            if part.role not in ('bubble','end-dialogue'):
                                previous_span_indices[(index,n)]=old_index
                                old_index+=1
                    if cache.fallback(bundle.item.mod_id,record.context,record.source):
                        record.manual_fallback=True
                        existing.add(index)
                        if bundle.project and record.adapter=='i18n':
                            entry=bundle.project.entries[record.context]
                            entry.target,entry.origin='', 'manual'
                        continue
                    baseline=cache.baseline(bundle.item.folder,record.context)
                    if baseline and baseline[0]!=record.source and record.target==baseline[1]:
                        record.target=''
                    if record.target.strip()==record.source.strip() and re.search('[A-Za-z]',record.source) and not re.search('[\u3400-\u9fff]',record.source) and record.source.strip() not in ('API','SMAPI','GMCM','JSON','NPC','OK'):
                        record.target=''
                    if not record.target and record.adapter=='gmcm':
                        record.target=display_target(bundle.item.mod_id,record.context,record.source)
                    if not record.target and already_chinese(record):
                        record.target=record.source
                        self.metrics['preserved_chinese']+=1
                    if record.target and has_incremental_event_text(record.source) and validate_rebuilt(record.source,record.target,record.kind):
                        old_parts,_=spans(record.target,record.kind)
                        # Recover paid/player edits, but do not treat previously
                        # newly parsed English event text as translated.
                        if any(a.text==b.text and any('A'<=c<='Z' or 'a'<=c<='z' for c in a.text) for a,b in zip(record.parts,old_parts)):
                            recovered_parts[index]={n:b.text for n,(a,b) in enumerate(zip(record.parts,old_parts)) if a.text!=b.text}
                            record.target=''
                    if record.target and validate_rebuilt(record.source,record.target,record.kind):
                        existing.add(index)
                    elif not record.parts and not record.notes:
                        record.target=record.source
                        existing.add(index)
                display_labels,unresolved_labels=minecart_labels(bundle)
                bundle.display_label_reuse=[]
                for index,(target,contexts) in display_labels.items():
                    record=bundle.records[index]
                    record.target=target;existing.add(index)
                    bundle.display_label_reuse.append({'context':record.context,'source_hash':source_hash(record.source),'target_hash':source_hash(target),'label_contexts':contexts})
                # Harvest all valid existing fields first, so conflicting player
                # edits disable reuse before any new field is assigned a target.
                cache.memory_put_many((key,bundle.records[index].target) for index in existing
                    if not getattr(bundle.records[index],'manual_fallback',False)
                    and (key:=memory_key(cache,self.config,bundle,bundle.records[index]))
                    and target_parts(bundle.records[index],bundle.records[index].target) is not None)
                for index,record in enumerate(bundle.records):
                    if index in existing:continue
                    key=memory_key(cache,self.config,bundle,record)
                    target=cache.memory_get(key) if key else None
                    if target and target_parts(record,target) is not None:
                        record.target=target;existing.add(index)
                        self.metrics['memory_hits']+=len(record_units.get(index,[]))
                for unit in bundle.units:
                    self.metrics['total']+=1
                    if unit.record in existing:
                        self.metrics['completed']+=1
                        self.metrics['cache_hits']+=1
                        continue
                    glossary=bundle.project.glossary if bundle.project else {}
                    key=cache.key(self.config,unit.mod_id,unit.context,unit.source,glossary)
                    if key in self.bindings:
                        self.bindings[key].append((bundle,unit))
                        if key in self.answers:
                            self.metrics['completed']+=1
                            self.metrics['cache_hits']+=1
                        continue
                    self.bindings[key]=[(bundle,unit)]
                    target=recovered_parts.get(unit.record,{}).get(unit.fragment)
                    if target is None:target=cache.get(key)
                    if target is None and (unit.record,unit.fragment) in previous_span_indices:
                        # New bubble spans shift later fragment indices. Recover
                        # the old exact source/context key without clearing cache.
                        old_index=previous_span_indices[(unit.record,unit.fragment)]
                        record=bundle.records[unit.record]
                        target=cache.get(cache.key(self.config,unit.mod_id,record.context+'/span/'+str(old_index),unit.source,glossary))
                    # Reuse an entire legacy candidate only if it can be split losslessly.
                    if target is None:
                        old=cache.legacy(unit.mod_id,bundle.records[unit.record].context,bundle.records[unit.record].source)
                        if old and validate_rebuilt(bundle.records[unit.record].source,old,bundle.records[unit.record].kind):
                            parts,_=spans(old,bundle.records[unit.record].kind)
                            if unit.fragment<len(parts):
                                target=parts[unit.fragment].text
                    if target==unit.source and re.search('[A-Za-z]',unit.source) and not re.search('[\u3400-\u9fff]',unit.source) and unit.source.strip() not in ('API','SMAPI','GMCM','JSON','NPC','OK'):
                        target=None
                    if target is not None and not validate_translation(unit.source,target):
                        self.answers[key]=target
                        self.metrics['completed']+=1
                        self.metrics['cache_hits']+=1
                    else:
                        diagnostic=cache.diagnostic(key)
                        if unit.record in unresolved_labels:
                            failure=Reason('矿车目的地标识缺少唯一可靠的显示名称；保留原值，等待名称映射，不自动请求翻译','alias_required')
                            self.errors[key]=failure
                            if not diagnostic or diagnostic['code']!='alias_required':
                                cache.record_failure([key],failure,exhausted=True)
                            self.metrics['failed']+=1
                        elif diagnostic and diagnostic['exhausted'] and not self.retry_exhausted:
                            self.errors[key]=Reason(diagnostic['reason'],diagnostic['code'])
                            self.metrics['failed']+=1
                        elif self.offline_only:
                            self.errors[key]=Reason('离线检查：缺少合格缓存译文，未发送请求','offline_missing')
                        else:
                            pending.append((key,unit.source))
                cache.mark_many(self.run_id,(key for key,source in pending),'queued')
                max_chars=min(6000,max(256,self.config.max_output_tokens//2))
                # Consolidate fragments from the same field with protected split
                # tokens. This avoids one HTTP item per word around placeholders.
                grouped={}
                for key,source in pending:
                    unit=self.bindings[key][0][1]
                    grouped.setdefault(unit.record,[]).append((key,source))
                canonical={}
                units_per_record=Counter(unit.record for unit in bundle.units)
                wire=[]
                for record,units in grouped.items():
                    identity=memory_key(cache,self.config,bundle,bundle.records[record])
                    # Merge only identical COMPLETE fields in a compatible role.
                    # Partial paid results and ambiguous short labels stay separate.
                    if identity and not cache.memory_blocked(identity) and len(units)==units_per_record[record]:
                        if identity in canonical:
                            original=canonical[identity]
                            for (old,_),(new,_) in zip(original,units):aliases.setdefault(old,[]).append(new)
                            self.metrics['duplicate_fragments']+=len(units)
                            continue
                        canonical[identity]=units
                    if identity:memory_records[identity]=(bundle,record,record_units[record])
                    field=bundle.records[record]
                    if whole_text(field) and len(units)==units_per_record[record] and fits(field.source,max_chars,self.config.max_output_tokens,self.marker_cost):
                        group='field-'+digest([key for key,source in units])
                        wire_groups[group]=units;whole_fields[group]=(field,record_units[record])
                        wire.append((group,field.source))
                        wire_contexts[group]=bundle.item.mod_id+' | '+field.context
                        continue
                    for chunk,marker,joined in fragment_groups(units,max_chars,self.config.max_output_tokens,self.marker_cost):
                        group='group-'+digest([key for key,source in chunk])
                        wire_groups[group]=chunk
                        wire_markers[group]=marker
                        wire.append((group,joined))
                        wire_contexts[group]=bundle.item.mod_id+' | '+bundle.records[record].context
                pending_by_mod.append(wire)
            for group,chunk in wire_groups.items():
                bundle,unit=self.bindings[chunk[0][0]][0]
                field=bundle.records[unit.record]
                wire_scopes[group]=(bundle.item.mod_id,field.adapter,field.kind)
            jobs=FairQueue(pending_by_mod,wire_scopes)
            plan={'source_records':self.metrics['source_records'],'text_fragments':self.metrics['total'],
                  'already_available_fragments':self.metrics['cache_hits'],'memory_hits':self.metrics['memory_hits'],
                  'duplicate_fragments':self.metrics['duplicate_fragments'],'preserved_chinese_fields':self.metrics['preserved_chinese'],
                  'unique_pending_fragments':sum(len(chunk) for chunk in wire_groups.values()),
                  'pending_fields':sum(len(items) for items in pending_by_mod),
                  'estimated_initial_requests':jobs.estimated_requests(max_chars,self.config.max_output_tokens,self.marker_cost) if bundles else 0,
                  'note':'片段数不是 HTTP 请求数；初始请求估算不含拆分、重试及批次调整。'}
            report['plan']=plan
            plan['bridge']=plugin_status(self.mods)
            atomic_write(path.with_name(self.run_id+'.plan.json'),json.dumps(plan,ensure_ascii=False,indent=2).encode('utf-8'))
            self.notify('translation_plan',plan)
            self.timings['planning_seconds']=time.monotonic()-planning_started
            if self.plan_decision is not None and jobs:
                confirmation_started=time.monotonic()
                import queue
                self.metrics['phase']='等待确认去重后的翻译用量';self.emit()
                while not self.stop.is_set():
                    try:
                        if not self.plan_decision.get_nowait():self.stop.set()
                        break
                    except queue.Empty:await asyncio.sleep(.1)
                self.timings['confirmation_wait_seconds']=time.monotonic()-confirmation_started
            self.metrics['phase']='并发翻译'
            self.translation_started=time.monotonic()
            network_failures=0
            async with Session(self.config,self.concurrency,self._transport_stats,self.transport,on_trace=self.trace,test_budget=self.test_budget) as session:
                session.contexts=wire_contexts
                active=set()
                paused=''
                def success(results,usage):
                    nonlocal network_failures
                    network_failures=0
                    expanded=[]
                    repairs=[]
                    validating=time.monotonic()
                    for group,source,target in results:
                        chunk=wire_groups[group]
                        pieces=[]
                        if group in whole_fields:
                            field,units=whole_fields[group]
                            parts=target_parts(field,target)
                            pieces=[parts[unit.fragment] for unit in units] if parts is not None else ['']*len(chunk)
                        else:
                            for n in range(1,len(chunk)):
                                piece,target=target.split('{{'+wire_markers[group]+'_'+str(n)+'}}',1)
                                pieces.append(piece)
                            pieces.append(target)
                        missing=[]
                        for (key,raw),value in zip(chunk,pieces):
                            issues=validate_translation(raw,value)
                            failure=Reason('字段修复仍未通过，保留原文待处理','structure')
                            if value.strip()==raw.strip() and re.search('[A-Za-z]',raw) and not re.search('[\u3400-\u9fff]',raw) and raw.strip() not in ('API','SMAPI','GMCM','JSON','NPC','OK'):
                                issues.append('模型未翻译此片段')
                                failure=Reason('模型仍原样返回此片段，保留原文待处理','unchanged')
                            if issues:
                                missing.append((key,raw))
                                repair_failures[key]=failure
                            else:expanded.append((key,raw,value))
                        for chunk,marker,joined in (([entry],'',entry[1]) for entry in missing):
                            retry='repair-'+uuid.uuid4().hex
                            wire_groups[retry]=chunk;wire_markers[retry]=marker
                            repair_failures[retry]=repair_failures[chunk[0][0]]
                            wire_contexts[retry]=wire_contexts[group];repairs.append((retry,joined))
                    expanded.extend((alias,source,target) for key,source,target in list(expanded) for alias in aliases.get(key,[]))
                    self.timings['validation_seconds']+=time.monotonic()-validating
                    writing=time.monotonic()
                    cache.put_many(self.run_id,expanded)
                    self.timings['cache_write_seconds']+=time.monotonic()-writing
                    for key,source,target in expanded:
                        if key not in self.answers:
                            if key in self.errors:
                                self.metrics['failed']-=len(self.bindings[key])
                                self.errors.pop(key,None)
                            self.answers[key]=target
                            self.metrics['completed']+=len(self.bindings[key])
                    self.emit()
                    return repairs
                def failed(items,error):
                    nonlocal network_failures,paused
                    if getattr(error,'code','unknown')=='connection':
                        network_failures+=1
                        if network_failures>=3:
                            paused='连续三个请求连接失败，后续请求暂停'
                    flat=[entry for group,source in items for entry in wire_groups[group]]
                    flat.extend((alias,source) for key,source in list(flat) for alias in aliases.get(key,[]))
                    for key,_ in flat:
                        if key not in self.errors and key not in self.answers:
                            self.metrics['failed']+=len(self.bindings[key])
                        self.errors[key]=error
                    cache.mark_many(self.run_id,(key for key,source in flat),'failed',error)
                    for key,_ in flat:
                        if key not in self.answers:
                            cache.record_failure([key],error,attempts.pop(key,0),getattr(error,'code','unknown') in CONTENT_CODES)
                    self.emit()
                async def execute(i,items):
                    nonlocal paused
                    cache.mark_many(self.run_id,(key for group,source in items for key,raw in wire_groups[group]),'in_flight')
                    good=bad=0
                    codes=[]
                    scope=wire_scopes[items[0][0]]
                    def ok(rows,usage):
                        nonlocal good
                        before=len(self.answers)
                        repairs=success(rows,usage)
                        good+=len(self.answers)-before
                        return repairs
                    def error(rows,reason):
                        nonlocal bad
                        bad+=len(rows);codes.extend([getattr(reason,'code','unknown')]*len(rows));failed(rows,reason)
                    def split_field(item,reason):
                        if getattr(reason,'code','unknown') not in ('structure','response_format','output_limit'):
                            return None
                        group,_=item
                        chunk=[entry for entry in wire_groups[group] if entry[0] not in self.answers]
                        if len(chunk)<2:return None
                        middle=len(chunk)//2
                        children=[]
                        for half in ([entry] for entry in chunk):
                            for subset,marker,joined in fragment_groups(half,session.batch_chars,self.config.max_output_tokens,self.marker_cost):
                                retry='split-'+uuid.uuid4().hex
                                wire_groups[retry]=subset;wire_markers[retry]=marker
                                wire_contexts[retry]=wire_contexts[group]
                                children.append((retry,joined))
                        return children
                    def attempted(rows):
                        for group,_ in rows:
                            for key,_ in wire_groups[group]:
                                attempts[key]+=1
                                cache.mark(self.run_id,key,'in_flight','请求已发起，结果及计费尚未确认')
                    await translate_recover(session,items,bundles[i].project.glossary if bundles[i].project else {},ok,error,split_item=split_field,on_attempt=attempted,
                                            repair_reason=lambda item:repair_failures[item[0]])
                    if circuits.finish(scope,good,codes):
                        paused='多个 mod 连续出现响应协议或结构异常，已暂停后续请求'
                        self.trace({'event':'circuit_pause','reason':paused})
                    if scope in circuits.paused:
                        self.trace({'event':'scope_pause','scope':list(scope),'reason':'连续四批没有有效新增译文'})
                try:
                    while jobs or active:
                        if self.stop.is_set() or paused:
                            for task in active:
                                task.cancel()
                            await asyncio.gather(*active,return_exceptions=True)
                            active.clear()
                            break
                        while jobs and len(active)<session.limit:
                            i,items=jobs.pop(session)
                            if wire_scopes[items[0][0]] in circuits.paused:
                                failed(items,Reason('同类内容连续失败，暂停此范围；其他 mod 继续','scope_paused'))
                                continue
                            active.add(asyncio.create_task(execute(i,items)))
                        if not active:
                            break
                        done,remaining=await asyncio.wait(active,timeout=.1,return_when=asyncio.FIRST_COMPLETED)
                        active=set(remaining)
                        for task in done:
                            try:
                                task.result()
                            except (ServiceError,BudgetStop) as exc:
                                paused=str(exc)
                finally:
                    for task in active:
                        task.cancel()
                    await asyncio.gather(*active,return_exceptions=True)
                if paused:
                    self.translation_paused=True
                    self.metrics['phase']='服务暂停：'+paused
                if paused or self.stop.is_set():
                    reason=paused or self.budget_reason or '用户停止；请求是否由服务端完成尚不能确认，等待玩家重试'
                    unresolved=[key for key in self.bindings if key not in self.answers]
                    for key in unresolved:
                        diagnostic=cache.diagnostic(key)
                        unknown=bool(attempts[key]) and key not in self.errors
                        cache.mark(self.run_id,key,'response_unknown' if unknown else 'pending_retry',
                                   diagnostic['reason'] if diagnostic else '请求结果及计费未知；'+reason if unknown else reason)
                        self.errors.setdefault(key,reason)
                    report['response_unknown_items']=sum(bool(attempts[key]) and key not in self.answers and not cache.diagnostic(key) for key in unresolved)
                    report['paused_reason']=reason
            self.timings['network_seconds']=time.monotonic()-self.translation_started
            memories=[]
            for identity,(bundle,index,units) in memory_records.items():
                record=bundle.records[index]
                glossary=bundle.project.glossary if bundle.project else {}
                keyed=[(cache.key(self.config,unit.mod_id,unit.context,unit.source,glossary),unit) for unit in units]
                values={str(unit.fragment):self.answers[key] for key,unit in keyed if key in self.answers}
                if len(values)==len(units):
                    target=rebuild(record.source,record.parts,values)
                    if target_parts(record,target) is not None:memories.append((identity,target))
            cache.memory_put_many(memories)
            self.metrics['phase']='保存/安装已校验结果'
            if paused:self.metrics['eta_seconds']=None
            self.emit()
            install_started=time.monotonic()
            conflicts=installation_conflicts(bundles)
            for index,bundle in enumerate(bundles):
                if self.test_budget and self.test_budget.remaining()<=0:self.stop.set()
                if self.stop.is_set():
                    report['failure'].append(self.result(bundle.item,'已暂停','用户停止；已返回的结果保存在 SQLite，未安装此 mod'))
                    continue
                result=self.result(bundle.item,'文件异常',conflicts[index]) if index in conflicts else self._install(bundle,index,cache)
                report['success' if result['status']=='成功' else 'failure'].append(result)
                atomic_write(path,json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
                self.notify('result',result)
            processed={r['folder'] for r in report['success']+report['failure']}
            for item in self.items:
                if str(item.folder) not in processed:
                    report['failure'].append(self.result(item,'已暂停','扫描被停止，保留原文件'))
            report['counts']=dict(Counter(r['status'] for r in report['success']+report['failure']))
            if self.test_budget:self.metrics['test_budget']=self.test_budget.snapshot()
            report['metrics']=dict(self.metrics)
            report['scope_pauses']=[list(scope) for scope in sorted(circuits.paused)]
            report['scope_pause_details']=[{'mod_id':scope[0],'adapter':scope[1],'text_kind':scope[2],
                                            'reason':'连续四个终结批次没有有效新增译文'}
                                           for scope in sorted(circuits.paused)]
            report['offline_only']=self.offline_only
            self.timings['install_seconds']=time.monotonic()-install_started
            report['timings']=dict(self.timings)
            report['elapsed_seconds']=round(time.monotonic()-self.started,2)
            atomic_write(path,json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
            lines=['ModLingo 结果：已生成、已安装、游戏内生效分别验收；待处理原因见同名 JSON。']+[r['name']+' · '+r['status']+' · '+r['reason'] for r in report['success']+report['failure']]
            atomic_write(path.with_suffix('.txt'),'\n'.join(lines).encode('utf-8'))
            return path,report
        finally:
            cache.close()

    def _transport_stats(self,stats):
        self.metrics.update(stats)

    @staticmethod
    def result(item,status,reason):
        return {'name':item.name,'mod_id':item.mod_id,'folder':str(item.folder),'status':status,'reason':reason,'installed':0,'skipped':[],'receipt':'','receipts':[],'in_game_verified':False}

    def _install(self,bundle,index,cache):
        result=self.result(bundle.item,'失败','没有可安装的译文')
        result['excluded_patches']=bundle.excluded_patches
        result['pending_captures']=bundle.pending_captures
        result['display_label_reuse']=getattr(bundle,'display_label_reuse',[])
        if bundle.category=='仅其他语言' and not bundle.units:
            return dict(self.result(bundle.item,'仅其他语言','已确认仅为其他语言专用补丁；不作为翻译成功，不发送请求'),excluded_patches=bundle.excluded_patches)
        if bundle.category=='文件异常' and not bundle.units:
            result.update(status='文件异常',reason='；'.join(bundle.notes))
            return result
        if bundle.category in ('无需翻译','待确认','需进游戏采集') and not bundle.units:
            result.update(status=bundle.category,reason='；'.join(bundle.notes) or ('无可提取文字；请安装插件采集' if bundle.category!='无需翻译' else '确认仅图像或非文本数据，无需请求 API'))
            return result
        bridge_records=[]
        observed=set()
        quality_warnings=[]
        numeric_review=set()
        completed=0
        completed_i18n=0
        units_by_record={}
        for unit in bundle.units:
            units_by_record.setdefault(unit.record,[]).append(unit)
        for record_index,record in enumerate(bundle.records):
            if getattr(record,'manual_fallback',False):
                result['skipped'].append({'key':record.context,'reason':'保留玩家主动选择的原文回退'})
                continue
            values={}
            missing=[]
            for unit in units_by_record.get(record_index,[]):
                key=cache.key(self.config,unit.mod_id,unit.context,unit.source,bundle.project.glossary if bundle.project else {})
                if key in self.answers:
                    values[str(unit.fragment)]=self.answers[key]
                else:
                    missing.append(self.errors.get(key,'尚未完成，保留原文待重试'))
            if record.target and validate_rebuilt(record.source,record.target,record.kind):
                target=record.target
                missing=[]
            elif not missing:
                try:
                    target=rebuild(record.source,record.parts,values)
                except ProjectError as exc:
                    target=''
                    missing.append(str(exc))
            else:
                target=''
            if missing or not validate_rebuilt(record.source,target,record.kind):
                result['skipped'].append({'key':record.context,'reason':missing[0] if missing else '重建后的脚本结构不匹配'})
                if bundle.project and record.adapter=='i18n':
                    bundle.project.entries[record.context].error='此项未完成，原值保留'
                continue
            completed+=1
            ordinal=re.match(r'^\s*\((\d+)\)',record.source)
            if ordinal and not re.match(r'^\s*\('+ordinal.group(1)+r'\)',target):
                numeric_review.add(record_index)
                quality_warnings.append({'context':record.context,'source_hash':source_hash(record.source),'code':'option_number_changed','reason':'选项序号与原文不同，需要人工核对'})
            elif record.context!='gmcm/mod-name' and re.search(r'[A-Za-z]{3,}',target):
                quality_warnings.append({'context':record.context,'source_hash':source_hash(record.source),'code':'latin_remaining','reason':'含英文，可能是专名或漏译；仅为检查线索'})
            if (bundle.item.mod_id,bundle.item.version,record.context,source_hash(record.source),source_hash(target)) in self.runtime_hits:
                observed.add((record.context,source_hash(record.source)))
            if record.notes:
                result['skipped'].append({'key':record.context,'reason':'；'.join(record.notes)})
            if record.adapter=='i18n':
                completed_i18n+=1
                entry=bundle.project.entries[record.context]
                entry.target,entry.error,entry.needs_review=target,'',False
                entry.translated_source=entry.source
            elif target != record.source:
                bridge_records.append({'adapter':record.adapter,'context':record.context,'asset':record.asset,'path':record.path,
                                       'source':record.source,'source_hash':source_hash(record.source),'target':target})
        receipts=[]
        try:
            validate_item(bundle.item,self.mods)
            validate_sources(bundle)
            if bundle.project and completed_i18n:
                details=install(bundle.item,bundle.project,self.mods,self.storage/'backups'/self.run_id/str(index),structured=True)
                result['installed']+=details['installed']
                result['skipped'].extend(details['skipped'])
                if details['receipt']:
                    receipts.append(details['receipt'])
                cache.installed_many(bundle.item.folder,bundle.project.entries.items())
            if bridge_records:
                result['bridge']=ensure_plugin(self.mods)
                receipt=install_dictionary(self.mods,bundle.item,bridge_records,self.storage/'backups'/self.run_id/(str(index)+'-bridge'),getattr(bundle,'bridge_snapshot',None))
                if receipt:
                    receipts.append(receipt)
                result['installed']+=len(bridge_records)
            if completed:
                result['status']='部分完成' if result['skipped'] or bundle.notes else '成功'
                result['reason']='已处理 %d/%d 个文本记录；游戏内生效待验证%s' % (completed,len(bundle.records),'；含插件译文，需启用采集插件' if bridge_records else '')
                if bundle.pending_captures:result['reason']+='；另有 %d 个混合 i18n 字段待游戏采集/条件版本验收' % len(bundle.pending_captures)
        except (ProjectError,OSError) as exc:
            result['reason']=str(exc)
        result['receipts']=receipts
        result['receipt']=receipts[0] if receipts else ''
        result['translation_state']='已生成译文' if completed else '待处理'
        result['installation_state']='已安装' if result['installed'] else '未安装'
        result['runtime_state']='未验证'
        if observed:result['runtime_state']='已观察到部分生效'
        result['runtime_observed_records']=len(observed)
        result['runtime_session_id']=self.runtime.get('session_id')
        result['quality_warnings']=quality_warnings
        if numeric_review and result['status']=='成功':result['status']='部分完成'
        result['diagnostic_count']=len(result['skipped'])
        skipped={}
        for detail in result['skipped']:
            entry=skipped.setdefault(detail['key'],{'key':detail['key'],'reason':detail['reason'],'reasons':[]})
            if detail['reason'] not in entry['reasons']:entry['reasons'].append(detail['reason'])
        result['skipped']=list(skipped.values())
        pending_records=len(bundle.records)-completed+len(numeric_review)
        result['pending_records']=pending_records
        result['reason']+='；已安装 %d 条；%s；待处理 %d 个文本记录' % (result['installed'],result['runtime_state'],pending_records)
        result['diagnostics']=[{'context':unit.context,'source_hash':source_hash(unit.source),**detail}
            for unit in bundle.units if (detail:=cache.diagnostic(cache.key(self.config,unit.mod_id,unit.context,unit.source,bundle.project.glossary if bundle.project else {})))]
        result['coverage']={'by_adapter':dict(Counter(record.adapter for record in bundle.records)), 'unknown_dynamic_coverage':True,'qualified_records':completed-len(numeric_review),'completed_records':completed,'pending_records':pending_records,'total_records':len(bundle.records),'category':bundle.category,
                            'pending_capture_fields':len(bundle.pending_captures),'observed_capture_paths':sum(p['observed_path'] for p in bundle.pending_captures)}
        return result

def run_engine(items,mods,config,stop,**kwargs):
    return asyncio.run(Engine(items,mods,config,stop,**kwargs).run())
