"""Empirical, conservative full-dataset forecast; never a fee guarantee."""
import hashlib
import json
from pathlib import Path
from .bridge_io import captures
from .discovery import scan_mods
from .extraction import extract
from .core import ProjectError,MASK_TOKEN
from .memory import role,already_chinese
from .test_snapshot import verify_snapshot

def forecast(snapshot,test_root):
    snapshot=Path(snapshot);test_root=Path(test_root);manifest=verify_snapshot(snapshot)
    samples=[];current=[]
    for output in test_root.glob('round-*'):
        path=output/'sample-sources.json'
        if not path.exists():continue
        sources=json.loads(path.read_text(encoding='utf-8'))
        chars=sum(len(MASK_TOKEN.sub('',source)) for context,source in sources)
        result_path=output/'result.json'
        if not chars or not result_path.exists():continue
        result=json.loads(result_path.read_text(encoding='utf-8'))
        levels=result.get('levels') or [result]
        for level in levels:
            if not level.get('all_sample_passed'):continue
            cost=level.get('conservative_cost_delta_cny',result.get('budget',{}).get('conservative_cny'))
            if cost is not None:
                samples.append(cost/chars)
                if level.get('wire_profile')=='deepseek-compact-v1':current.append(cost/chars)
    if not samples:raise ProjectError('尚无成功真实样本，不能预测全量费用')
    offline=captures(snapshot/'Mods');seen=set();chars=fields=units=0;gaps=[]
    for item in scan_mods(snapshot/'Mods'):
        if not item.mod_id:continue
        try:
            bundle=extract(item,offline)
            if bundle.category not in ('可直接翻译','无需翻译','仅其他语言'):
                gaps.append({'mod_id':item.mod_id,'category':bundle.category,'notes':bundle.notes})
            for record in bundle.records:
                if record.target or already_chinese(record):continue
                identity=(item.mod_id,role(record) or record.context,record.source)
                if identity in seen:continue
                seen.add(identity)
                parts=[part for part in record.parts if any('A'<=c<='Z' or 'a'<=c<='z' for c in part.text)]
                if not parts:continue
                fields+=1;units+=len(parts);chars+=sum(len(part.text) for part in parts)
        except (ProjectError,OSError,ValueError) as exc:
            gaps.append({'mod_id':item.mod_id,'category':'文件异常','notes':[str(exc)]})
    # Failed original extraction may have frozen only the manifest. A later
    # successful parse of that reduced fixture must not erase original gaps.
    gap_ids={row['mod_id'] for row in gaps}
    for original in manifest['inventory']:
        if original.get('category')=='文件异常' and original['mod_id'] not in gap_ids:
            gaps.append({'mod_id':original['mod_id'],'category':original.get('category','待确认'),
                         'notes':original.get('notes',[]),'origin':'frozen_original_inventory'})
            gap_ids.add(original['mod_id'])
    estimate=chars*max(current or samples)
    return {'kind':'preflight_no_paid_calls','source_mods':len(manifest['inventory']),'unique_pending_fields':fields,
            'unique_pending_fragments':units,'pending_human_characters':chars,
            'estimated_conservative_cny':round(estimate,4),'estimated_offpeak_bill_cny':round(estimate/2,4),
            'within_round_budget_estimate':estimate<=2.7,'coverage_preflight_clear':not gaps,
            'current_wire_profile_measured':bool(current),
            'cold_acceptance_allowed':bool(current) and estimate<=2.7 and not gaps,'coverage_gaps':gaps,
            'snapshot_sha256':hashlib.sha256(json.dumps(manifest['checksums'],sort_keys=True).encode()).hexdigest(),
            'note':'按成功样本最高费用/原文字符比率外推，非报价或账单保证；待采集、未知语法及文件异常仍单列。'}
