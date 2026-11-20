"""Bound actual joined/encoded text, including protected-token overhead."""
import math
from .core import MASK_TOKEN

def output_cost(source,marker_cost=22):
    if marker_cost==8 and '__ML_' in source:marker_cost=22
    protected=list(MASK_TOKEN.finditer(source))
    plain=len(source)-sum(m.end()-m.start() for m in protected)
    # Conservative heuristic, not a vendor tokenizer or cost estimate. Random
    # masked markers are token-heavy; reserve room for the JSON envelope.
    return math.ceil(plain*1.5)+len(protected)*marker_cost+24

def fits(source,max_chars,max_tokens,marker_cost=22):
    return len(source)<=max_chars and output_cost(source,marker_cost)<=max(64,int(max_tokens*.75))

def wire_batches(items,max_items=20,max_chars=6000,max_tokens=8192,marker_cost=22):
    if marker_cost==8:
        items=list(items)
        if any('__ML_' in source for key,source in items):marker_cost=22
    batch=[];chars=cost=0
    for item in items:
        size=len(item[1]);weight=output_cost(item[1],marker_cost)
        if batch and (len(batch)>=max_items or chars+size>max_chars or cost+weight>max(64,int(max_tokens*.75))):
            yield batch;batch=[];chars=cost=0
        batch.append(item);chars+=size;cost+=weight
    if batch:yield batch

def fragment_groups(units,max_chars,max_tokens,marker_cost=22):
    # Account for the actual separators, rather than only original characters.
    import uuid
    marker='MLJOIN_'+uuid.uuid4().hex[:12]
    while any(marker in raw for _,raw in units):marker='MLJOIN_'+uuid.uuid4().hex[:12]
    chunk=[];joined=''
    for unit in units:
        candidate=joined+('{{'+marker+'_'+str(len(chunk))+'}}' if chunk else '')+unit[1]
        if chunk and (len(chunk)>=20 or not fits(candidate,max_chars,max_tokens,marker_cost)):
            yield chunk,marker,joined
            chunk=[];joined='';candidate=unit[1]
        chunk.append(unit);joined=candidate
    if chunk:yield chunk,marker,joined
