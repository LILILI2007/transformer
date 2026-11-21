"""Same-run, evidence-based derived display tasks. No identifier is API prose."""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import re

from .compatibility import minecart_labels
from .extraction import source_hash
from .minecarts import ASSET, display_path, resolve_names
from .syntax import rebuild, validate_rebuilt


@dataclass
class Task:
    key: tuple
    requires: set = field(default_factory=set)
    state: str = 'waiting'
    reason: str = ''


class DependencyGraph:
    """Small reusable task graph; providers complete before derived tasks."""
    def __init__(self):
        self.tasks = {}

    def add(self, key, requires=()):
        task = self.tasks.setdefault(key, Task(key))
        task.requires.update(requires)
        for dependency in requires:
            self.tasks.setdefault(dependency, Task(dependency))
        return task

    def cycles(self):
        # Iterative Kosaraju: include every member of overlapping cycles,
        # while leaving downstream consumers outside the cyclic component.
        visited, order = set(), []
        reverse = defaultdict(set)
        for key, task in self.tasks.items():
            for child in task.requires:reverse[child].add(key)
        for start in self.tasks:
            if start in visited:continue
            stack = [(start, False)]
            while stack:
                key, done = stack.pop()
                if done:
                    order.append(key);continue
                if key in visited:continue
                visited.add(key)
                stack.append((key, True))
                stack.extend((child, False) for child in self.tasks[key].requires if child not in visited)
        visited, cycles = set(), set()
        for start in reversed(order):
            if start in visited:continue
            component, stack = set(), [start]
            while stack:
                key = stack.pop()
                if key in visited:continue
                visited.add(key);component.add(key)
                stack.extend(reverse[key] - visited)
            if len(component)>1 or start in self.tasks[start].requires:
                cycles.update(component)
        return cycles



class NameDependencies:
    """Name references are AND dependencies: every definition must agree.

    A failed/missing/conflicting provider leaves consumers pending. Refresh is
    event-driven by provider fragments, then repeated once before installation.
    """
    def __init__(self, bundles, cache, config, answers):
        self.bundles, self.cache, self.config, self.answers = bundles, cache, config, answers
        self.graph = DependencyGraph()
        self.waiting = {}
        self.provider_records = {}
        self.units = {}
        self.provider_keys = defaultdict(set)
        self.local_providers = defaultdict(set)
        self.backfilled = 0
        self.refreshes = 0
        self.names = resolve_names(bundles, cache, config, answers)
        for bundle in bundles:
            by_context = {r.context: r for r in bundle.records if r.adapter == 'i18n'}
            destinations = {r.source for r in bundle.records if r.adapter == 'i18n'
                and re.fullmatch(r'config\.\d+\.\.values\..+', r.context)}
            references_by_key, references_by_path = defaultdict(list), defaultdict(list)
            for reference in bundle.minecart_names:
                if reference['kind'] == 'i18n':references_by_key[reference['key'].casefold()].append(reference)
                references_by_path[tuple(reference['path'])].append(reference)
            unit_map = defaultdict(list)
            for unit in bundle.units:unit_map[unit.record].append(unit)
            for index, record in enumerate(bundle.records):
                path = display_path(record.path) if record.asset == ASSET else None
                references = (references_by_key.get(record.context.casefold(), []) if record.adapter == 'i18n' else []) + references_by_path.get(tuple(path or []), [])
                # Legacy names used by the original minecart pack are providers
                # too; their validated current-run targets must be materialized.
                tooltip = by_context.get(record.context[:-5]+'.description') if record.context.startswith('config.Enable') and record.context.endswith('.name') else None
                local = (bundle.item.mod_id == 'jibb.minecarts' and record.adapter == 'i18n'
                    and (record.context in destinations or tooltip and tooltip.source in destinations
                        and not record.source.lower().startswith(('enable ', 'disable '))))
                if not references and not local:continue
                node = ('field', str(bundle.item.folder), index)
                identifiers = {r['destination'] for r in references}
                if local:
                    identifiers.add(record.context)
                    if record.context.startswith('config.Enable') and record.context.endswith('.name'):
                        if tooltip:identifiers.add(tooltip.source)
                self.provider_records[node] = (bundle, index)
                self.units[node] = unit_map[index]
                self.graph.add(node)
                for identifier in identifiers:
                    self.graph.add(('name', identifier), [node])
                    self.local_providers[identifier].add(node)
        # Bind fragment events only for records that can actually provide names.
        for node, (bundle, index) in self.provider_records.items():
            glossary = bundle.project.glossary if bundle.project else {}
            for unit in self.units[node]:
                key = cache.key(config, unit.mod_id, unit.context, unit.source, glossary)
                self.provider_keys[key].add(node)

    def defer(self, bundle, index, keys):
        node = ('field', str(bundle.item.folder), index)
        record = bundle.records[index]
        self.graph.add(node, [('name', record.source)])
        self.waiting[node] = (bundle, index, list(keys))

    def _materialize(self):
        for node, (bundle, index) in self.provider_records.items():
            record = bundle.records[index]
            if getattr(record, 'manual_fallback', False):continue
            if record.target and validate_rebuilt(record.source, record.target, record.kind):
                self.graph.tasks[node].state = 'completed';continue
            glossary = bundle.project.glossary if bundle.project else {}
            values = {}
            units = self.units[node]
            for unit in units:
                key = self.cache.key(self.config, unit.mod_id, unit.context, unit.source, glossary)
                if key not in self.answers:break
                values[str(unit.fragment)] = self.answers[key]
            else:
                if units:
                    target = rebuild(record.source, record.parts, values)
                    if validate_rebuilt(record.source, target, record.kind):
                        record.target = target
                        self.graph.tasks[node].state = 'completed'

    def refresh(self, changed=None):
        if changed is not None and not self.provider_keys.keys() & set(changed):return []
        self.refreshes += 1
        self._materialize()
        self.names = resolve_names(self.bundles, self.cache, self.config, self.answers)
        cycles = self.graph.cycles()
        ready = []
        grouped = defaultdict(list)
        for node, entry in self.waiting.items():grouped[id(entry[0])].append((node, entry))
        for entries in grouped.values():
            bundle = entries[0][1][0]
            resolved, _ = minecart_labels(bundle, self.names)
            for node, (_, index, keys) in entries:
                if node in cycles and bundle.records[index].source not in self.names.names:
                    self.graph.tasks[node].reason = '名称依赖形成循环，保留原值'
                    continue
                if index not in resolved:continue
                target, contexts = resolved[index]
                record = bundle.records[index]
                record.target = target
                bundle.display_label_reuse.append({'context': record.context,
                    'source_hash': source_hash(record.source), 'target_hash': source_hash(target),
                    'label_contexts': contexts, 'same_run_dependency': True})
                self.graph.tasks[node].state = 'completed'
                self.graph.tasks[('name', record.source)].state = 'completed'
                self.backfilled += 1
                ready.append((node, bundle, index, keys))
        for node, _, _, _ in ready:del self.waiting[node]
        return ready

    def finish(self, errors, stopped=False):
        failed_nodes = set().union(*(nodes for key, nodes in self.provider_keys.items() if key in errors))
        for node, (bundle, index, keys) in self.waiting.items():
            task = self.graph.tasks[node]
            identifier = bundle.records[index].source
            providers = self.graph.tasks[('name', identifier)].requires
            failed = bool(failed_nodes & providers)
            task.reason = task.reason or ('任务已暂停，名称依赖尚未完成' if stopped else
                '名称提供者翻译失败，保留原值待重试' if failed else
                self.names.reasons.get(identifier) or '缺少名称提供者或唯一可靠的中文显示名称')
            task.state = 'waiting' if stopped else 'blocked'

    def summary(self):
        reasons = Counter()
        for node in self.waiting:reasons[self.graph.tasks[node].reason or '等待名称提供者'] += 1
        return {'same_run_backfilled_fields': self.backfilled, 'pending_fields': len(self.waiting),
            'provider_fields': len(self.provider_records),
            'edges': sum(len(task.requires) for task in self.graph.tasks.values()),
            'refreshes': self.refreshes, 'pending_reasons': dict(reasons)}

    def validation_providers(self):
        providers = {identifier: list(bundles) for identifier, bundles in self.names.providers.items()}
        for identifier, nodes in self.local_providers.items():
            providers.setdefault(identifier, []).extend(self.provider_records[node][0] for node in nodes)
        return providers
