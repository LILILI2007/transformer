"""Typed minecart display names. Destination IDs and player values never change."""
from collections import defaultdict
from dataclasses import dataclass, field
import re

ASSET = 'Data/Minecarts'
from .resource_rules import ID as _ID, I18N as _I18N, LOCALIZED as _LOCALIZED, text_path as resource_text_path, reference_kind


def display_path(pointer):
    path=resource_text_path(ASSET,pointer)
    return path if path and len(path)==4 and path[-1]=='DisplayName' else None


def text_path(pointer):
    return resource_text_path(ASSET,pointer)


def name_reference(pointer, value, origin):
    path = display_path(pointer)
    if path is None:
        return None
    kind, key = 'unresolved', ''
    if isinstance(value, str) and value.strip():
        i18n = _I18N.fullmatch(value)
        localized = _LOCALIZED.fullmatch(value)
        if i18n:
            kind, key = 'i18n', i18n.group(1).strip()
        elif localized:
            kind, key = 'localized', localized.group(1)
        elif reference_kind(value)=='literal':
            kind = 'literal'
    return {'network':path[0], 'destination':path[2], 'path':path,
            'kind':kind, 'key':key, 'source':value, 'origin':origin}


@dataclass
class NameIndex:
    names: dict = field(default_factory=dict)
    unresolved: set = field(default_factory=set)
    reasons: dict = field(default_factory=dict)
    providers: dict = field(default_factory=dict)


def resolve_names(bundles, cache=None, config=None, answers=None):
    """Accept a name only when every known definition resolves without conflict.

    Reads declared DisplayName fields across loaded packs, not arbitrary matching
    strings. LocalizedText observations must match the current definition bytes.
    Validated results from this run can satisfy references before installation.
    """
    from .core import validate_translation
    from .extraction import source_hash
    from .syntax import rebuild, validate_rebuilt
    definitions = defaultdict(list)
    providers = defaultdict(dict)
    for bundle in bundles:
        if not getattr(bundle, 'minecart_names', []):continue
        by_context = defaultdict(list)
        by_asset = defaultdict(list)
        units = defaultdict(list)
        for index, record in enumerate(bundle.records):
            by_context[record.context.casefold()].append((index, record))
            if record.asset == ASSET and display_path(record.path):
                by_asset[tuple(display_path(record.path))].append((index, record))
        for unit in bundle.units:
            units[unit.record].append(unit)
        memo = {}

        def target(index, record):
            if index in memo:
                return memo[index]
            result = None
            manual = getattr(record, 'manual_fallback', False)
            if cache is not None:
                manual = manual or cache.fallback(bundle.item.mod_id, record.context, record.source)
            if not manual and not record.notes and record.kind == 'text':
                value = record.target
                if cache is not None:
                    baseline = cache.baseline(bundle.item.folder, record.context)
                    if baseline and baseline[0] != record.source and value == baseline[1]:
                        value = ''
                if not value and re.search(r'[\u3400-\u9fff]', record.source) and not re.search('[A-Za-z]', record.source):
                    value = record.source
                if not value and cache is not None and config is not None:
                    values = {}
                    glossary = bundle.project.glossary if bundle.project else {}
                    for unit in units[index]:
                        key = cache.key(config, unit.mod_id, unit.context, unit.source, glossary)
                        part = answers.get(key) if answers is not None else None
                        if part is None:part = cache.get(key)
                        if part is None or validate_translation(unit.source, part):
                            break
                        values[str(unit.fragment)] = part
                    else:
                        if values:
                            value = rebuild(record.source, record.parts, values)
                if value and re.search(r'[\u3400-\u9fff]', value) and validate_rebuilt(record.source, value, record.kind):
                    result = value
            memo[index] = result
            return result

        for reference in getattr(bundle, 'minecart_names', []):
            path = display_path(reference.get('path', []))
            if path is None:
                continue
            choices = set()
            proof = set()
            raw = reference.get('source')
            if reference.get('kind') == 'literal':
                for index, record in by_asset[tuple(path)]:
                    if record.source == raw and (value := target(index, record)):
                        choices.add(value)
                        proof.add(record.context)
            elif reference.get('kind') == 'i18n':
                for index, record in by_context.get(reference.get('key','').casefold(), []):
                    if record.adapter != 'i18n':
                        continue
                    if value := target(index, record):
                        choices.add(value)
                        proof.add(record.context)
                    # CP's resolved asset may have been translated by Bridge.
                    for observed_index, observed in by_asset[tuple(path)]:
                        if observed.source == record.source and (value := target(observed_index, observed)):
                            choices.add(value)
                            proof.add(observed.context)
            elif reference.get('kind') == 'localized':
                for observation in getattr(bundle, 'minecart_name_observations', []):
                    value = observation.get('resolved_name', '')
                    if (observation.get('path') == path and observation.get('source') == raw
                            and observation.get('source_hash') == source_hash(raw)
                            and observation.get('locale') == 'zh' and isinstance(value, str)
                            and re.search(r'[\u3400-\u9fff]', value)
                            and observation.get('resolved_hash') == source_hash(value)
                            and not re.search(r'[{}\[\]]',value)):
                        choices.add(value)
                        proof.add(observation['context'])
            evidence = [bundle.item.mod_id+':'+reference['origin']+':'+p for p in sorted(proof or {'/'.join(path)})]
            definitions[path[2]].append((choices, evidence, reference.get('kind', 'unresolved')))
            providers[path[2]][str(bundle.item.folder)] = bundle

    result = NameIndex()
    for identifier, variants in definitions.items():
        names = set().union(*(names for names, _, _ in variants))
        if all(len(names) == 1 for names, _, _ in variants) and len(names) == 1:
            result.names[identifier] = (next(iter(names)), sorted({p for _, evidence, _ in variants for p in evidence}))
            result.providers[identifier] = list(providers[identifier].values())
        else:
            result.unresolved.add(identifier)
            result.reasons[identifier] = ('名称定义存在冲突' if len(names) > 1
                else '动态或自定义显示名称，保留原值' if any(kind == 'unresolved' for _, _, kind in variants)
                else '显示名称缺少可靠中文或游戏本地化证据')
    return result
