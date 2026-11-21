"""Resource contract shared verbatim with the embedded Bridge resource.

These decisions concern data fields, stable IDs and reference classification;
the Python syntax adapters still own script segmentation and reconstruction.
"""
import hashlib
import json
from pathlib import Path
import re

_RAW = Path(__file__).with_name('resource_rules.json').read_bytes()
RULES = json.loads(_RAW)
VERSION = RULES['version']
HASH = hashlib.sha256(_RAW).hexdigest()
VISIBLE_FIELDS = frozenset(RULES['visible_fields'])
TEXT_ASSETS = tuple(rule['name'] for rule in RULES['assets'])
KNOWN_LANGUAGES = frozenset(RULES['known_languages'])
ID = re.compile(RULES['id_pattern'])
I18N = re.compile(RULES['i18n_pattern'], re.I)
LOCALIZED = re.compile(RULES['localized_pattern'])


def asset_rule(asset):
    if not isinstance(asset, str) or '{{' in asset:
        return None
    asset = asset.replace('\\', '/')
    for rule in RULES['assets']:
        name = rule['name']
        if (asset.casefold().startswith(name.casefold()) if name.endswith('/')
                else asset.casefold() == name.casefold()):
            return rule
    return None


def canonical_asset(asset):
    rule = asset_rule(asset)
    return rule['name'] + asset.replace('\\', '/')[len(rule['name']):] if rule else asset


def _match(pointer, pattern, strict=True):
    if not isinstance(pointer, (list, tuple)) or len(pointer) != len(pattern):
        return None
    result = []
    for value, field in zip(pointer, pattern):
        if not isinstance(value, str) or '\x1f' in value:
            return None
        if field == '*':
            if strict and not ID.fullmatch(value):
                return None
            result.append(value)
        elif value.casefold() == field.casefold():
            result.append(field)
        else:
            return None
    return result


def text_path(asset, pointer, strict=True):
    rule = asset_rule(asset)
    if rule is None or not isinstance(pointer, (list, tuple)) or not pointer:
        return None
    if len(pointer) > RULES['max_depth'] or any(not isinstance(p, str) or '{{' in p or '\x1f' in p for p in pointer):
        return None
    if rule['mode'] == 'paths':
        return next((path for pattern in rule['paths'] if (path := _match(pointer, pattern, strict)) is not None), None)
    if (rule['mode'] == 'all' or pointer[-1] in VISIBLE_FIELDS
            or pointer[-1] == 'Name' and rule['name'] in RULES['name_assets']):
        return list(pointer)
    return None


def list_mode(asset, pointer):
    rule = asset_rule(asset)
    if rule is None:
        return 'skip'
    if rule['mode'] != 'paths':
        return 'index'
    return 'id' if any(_match(pointer, pattern) is not None for pattern in rule['id_lists']) else 'skip'


def list_ids(asset, pointer, children):
    mode = list_mode(asset, pointer)
    if mode == 'skip':
        return None
    if mode == 'index':
        return [str(n) for n in range(len(children))]
    ids = [next((v for k, v in child.items() if k.casefold() == 'id'), None)
           if isinstance(child, dict) else None for child in children]
    return ids if all(isinstance(key, str) and ID.fullmatch(key) for key in ids) and len(set(ids)) == len(ids) else None


def reference_kind(value):
    if not isinstance(value, str) or not value.strip():
        return 'unresolved'
    if I18N.fullmatch(value):
        return 'i18n'
    if LOCALIZED.fullmatch(value):
        return 'localized'
    return 'unresolved' if '{{' in value or '[' in value else 'literal'


def unrelated_language(change):
    if change.get('Action') not in ('Include', 'EditData', 'Load', 'EditImage', 'EditMap'):
        return ''
    when = change.get('When')
    language = when.get('Language') if isinstance(when, dict) else None
    if isinstance(language, str):
        values = [value.strip() for value in language.split(',')]
        if values and all(value in KNOWN_LANGUAGES for value in values) and not set(values) & {'en', 'zh'}:
            return 'When.Language=' + ','.join(values)
    locale = change.get('TargetLocale')
    if change.get('Action') == 'EditData' and isinstance(locale, str) and locale in KNOWN_LANGUAGES - {'en', 'zh'}:
        return 'TargetLocale=' + locale
    return ''
