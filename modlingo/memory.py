"""Conservative whole-field memory; never merge independent short fragments."""
import re
from .core import MASK_TOKEN,_unsupported_reason
from .syntax import spans, validate_rebuilt

def role(record):
    if record.notes or record.kind!='text':return None
    # Same CP pack, identical enumerated choices and exact source: stable UI
    # semantics across repeated destination slots, without merging other labels.
    if record.origin.startswith('cp-config/value/'):
        return record.adapter+'|'+record.origin
    # Short labels can have unrelated meanings (e.g. Mine). Keep their context.
    words=re.findall(r'[A-Za-z]+',MASK_TOKEN.sub('',record.source))
    if len(words)<6 or sum(map(len,words))<30:return None
    if record.asset:
        return record.adapter+'|'+record.asset
    # Keep the first two namespaces, including dialogue speaker / UI section.
    namespace=re.split(r'[./:_-]',record.context.casefold())
    return record.adapter+'|'+'.'.join(namespace[:2])

def memory_key(cache,config,bundle,record):
    scope=role(record)
    if scope is None:return None
    return cache.key(config,bundle.item.mod_id,'whole-memory-v1|'+scope,
                     record.source,bundle.project.glossary if bundle.project else {})

def target_parts(record,target):
    if not validate_rebuilt(record.source,target,record.kind):return None
    parts,notes=spans(target,record.kind)
    if notes or len(parts)!=len(record.parts):return None
    return [part.text for part in parts]

def already_chinese(record):
    human=''.join(part.text for part in record.parts)
    return bool(re.search(r'[\u3400-\u9fff]',human)) and not re.search(r'[A-Za-z]',human)

def whole_text(record):
    if record.kind!='text' or record.notes or _unsupported_reason(record.source):return False
    # A syntax adapter may protect more than MASK_TOKEN (mail attachments etc.).
    # Such commands must never be exposed as ordinary human text to the model.
    cursor=0
    for part in record.parts:
        if re.search(r'[A-Za-z0-9]',MASK_TOKEN.sub('',record.source[cursor:part.start])):return False
        cursor=part.end
    return not re.search(r'[A-Za-z0-9]',MASK_TOKEN.sub('',record.source[cursor:]))
