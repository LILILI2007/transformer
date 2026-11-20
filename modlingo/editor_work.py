"""Concurrent, resumable single-mod preview; never installs a mod."""
import asyncio
from .async_provider import Session, ServiceError, translate_recover
from .cache import Cache
from .provider import batches
from .batching import wire_batches
from .adaptive import FairQueue
from .syntax import spans, kind_for, rebuild, validate_rebuilt

async def translate_editor(items,mod_id,config,glossary,stop,storage,notify,concurrency=4):
    cache=Cache(storage)
    pending=[];answers={};parts_by_key={};sources=dict(items);lookup={}
    try:
        for key,source in items:
            parts,notes=spans(source,kind_for(source));parts_by_key[key]=parts
            for n,part in enumerate(parts):
                identity=cache.key(config,mod_id,key+'/span/'+str(n),part.text,glossary)
                lookup.setdefault(identity,[]).append((key,str(n),part.text))
                target=cache.get(identity)
                if target is not None:
                    answers[identity]=target
                elif len(lookup[identity])==1:
                    pending.append((identity,part.text))
        def success(results,usage):
            for identity,source,target in results:
                cache.put(identity,target);answers[identity]=target
            notify('progress',(len(answers),len(lookup)))
        def failed(items,error):
            keys=list(dict.fromkeys(key for identity,_ in items for key,_,_ in lookup[identity]))
            notify('errors',(keys,error))
        async with Session(config,concurrency,lambda stats:notify('editor_stats',stats)) as session:
            session.contexts={identity:mod_id+' | '+rows[0][0] for identity,rows in lookup.items()}
            queue=FairQueue([pending])
            active=set()
            while queue or active:
                if stop.is_set():
                    for task in active:task.cancel()
                    await asyncio.gather(*active,return_exceptions=True)
                    break
                while queue and len(active)<session.limit:
                    _,batch=queue.pop(session)
                    active.add(asyncio.create_task(translate_recover(session,batch,glossary,success,failed)))
                done,remaining=await asyncio.wait(active,timeout=.1,return_when=asyncio.FIRST_COMPLETED)
                active=set(remaining)
                for task in done:
                    try:task.result()
                    except ServiceError as exc:
                        for other in active:other.cancel()
                        await asyncio.gather(*active,return_exceptions=True)
                        active.clear();queue=FairQueue([]);notify('errors',([k for k,_ in items],str(exc)))
        values={}
        for identity,target in answers.items():
            for key,n,_ in lookup[identity]:values.setdefault(key,{})[n]=target
        results=[]
        for key,parts in parts_by_key.items():
            if len(values.get(key,{}))==len(parts) and parts:
                target=rebuild(sources[key],parts,values.get(key,{}))
                if validate_rebuilt(sources[key],target,kind_for(sources[key])):
                    results.append((key,sources[key],target))
        # Results are applied once; the SQLite checkpoint is already durable.
        for i in range(0,len(results),200):notify('results',results[i:i+200])
    finally:
        cache.close()
