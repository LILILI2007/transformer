"""Small reviewed display rules, never config-value or source rewrites."""
import json
import sys
import re
from pathlib import Path
from functools import lru_cache


@lru_cache(maxsize=1)
def display_rules():
    path=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))/'bridge/package/display.zh.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def display_target(mod, context, source):
    return next((r['target'] for r in display_rules() if
                 (r['mod_id'],r['context'],r['source'])==(mod,context,source)), '')


def minecart_labels(bundle, names=None):
    """Reuse this pack's own named destinations, never translate config IDs.

    Only the known numbered destination selectors and matching tooltips are
    eligible. Conflicting names stay unresolved, as do unknown destinations.
    """
    if bundle.item.mod_id!='jibb.minecarts':return {},set()
    from .syntax import validate_rebuilt
    records=bundle.records
    by_key={r.context:r for r in records if r.adapter=='i18n'}
    destinations={r.source for r in records if r.adapter=='i18n'
                  and re.fullmatch(r'config\.\d+\.\.values\..+',r.context)}
    candidates={};incomplete=set()
    if names is not None:
        for raw,(target,contexts) in names.names.items():
            candidates.setdefault(raw,{})[target]=list(contexts)
    def add(raw,record):
        if record and not getattr(record,'manual_fallback',False) and record.target and re.search('[\u3400-\u9fff]',record.target) and validate_rebuilt(record.source,record.target,record.kind):
            candidates.setdefault(raw,{}).setdefault(record.target,[]).append(record.context)
        elif record and (names is None or raw not in names.names):
            # A missing legacy menu/mod title is not an independent conflicting
            # destination definition when typed resource evidence already exists.
            incomplete.add(raw)
    for raw in destinations:
        add(raw,by_key.get(raw))
    for record in records:
        if record.adapter=='i18n' and record.context.startswith('config.Enable') and record.context.endswith('.description') and record.source in destinations:
            name=by_key.get(record.context[:-len('.description')]+'.name')
            # Do not mistake an enable/disable instruction for a place name.
            if name and not name.source.lower().startswith(('enable ','disable ')):
                add(record.source,name)
    resolved={};pending=set()
    for index,record in enumerate(records):
        eligible=(record.adapter=='i18n' and (re.fullmatch(r'config\.\d+\.\.values\..+',record.context)
                   or record.context in destinations
                   or (record.context.startswith('config.Enable') and record.context.endswith('.description'))))
        eligible=eligible or (record.adapter=='gmcm' and re.match(r'^gmcm/Add(?:TextOption|BoolOption)/(?:formatAllowedValue|tooltip)(?:/|$)',record.context))
        if not eligible or record.source not in destinations or getattr(record,'manual_fallback',False):continue
        if record.target:continue
        if record.source in incomplete or names is not None and record.source in names.unresolved:
            pending.add(index)
            continue
        choices=candidates.get(record.source,{})
        if len(choices)==1:
            target,contexts=next(iter(choices.items()))
            if validate_rebuilt(record.source,target,record.kind):
                resolved[index]=(target,sorted(contexts));continue
        pending.add(index)
    return resolved,pending
