"""Assemble a checked local release without copying player data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    out=args.output.resolve()
    reports=out/'ModLingo/reports'
    reports.mkdir(exist_ok=True)
    for name in ['game-probe-summary-0.6.13.json','performance-0.6.13.json','regression-0.6.13.json','coverage-0.6.13.json','coverage-0.6.13.csv']:
        shutil.copy2(ROOT/'artifacts'/name,reports/name)
    regression=json.loads((reports/'regression-0.6.13.json').read_text())
    probe=json.loads((reports/'game-probe-summary-0.6.13.json').read_text(encoding='utf-8'))
    coverage=json.loads((reports/'coverage-0.6.13.json').read_text(encoding='utf-8'))
    assert regression['passed'] and all(probe['known_menu_checks'].values())
    assert len(probe['config_bytes_unchanged'])==3 and all(probe['config_bytes_unchanged'].values())
    assert not coverage['requests'] and not coverage['player_files_changed'] and not coverage['counts'].get('\u5931\u8d25')
    shutil.copy2(ROOT/'release/ModLingo-source.zip',out/'ModLingo-source.zip')
    for p in (ROOT/'docs').glob('*.md'):shutil.copy2(p,out/'ModLingo/docs'/p.name)
    dlls=[out/'ModLingo.Bridge/ModLingo.Bridge.dll',out/'ModLingo/_internal/bridge/package/ModLingo.Bridge.dll',ROOT/'bridge/package/ModLingo.Bridge.dll']
    hashes=[hashlib.sha256(p.read_bytes()).hexdigest() for p in dlls]
    assert len(set(hashes))==1
    for p in out.rglob('*'):
        if p.is_file():assert p.name not in ['captures.json','settings.json','translations.sqlite3','budget.sqlite3','test-budget.json'],p
    with zipfile.ZipFile(out/'ModLingo-source.zip') as z:
        assert z.testzip() is None
        assert not any('/captures.json' in n or n.endswith('.sqlite3') for n in z.namelist())
    delivery=dict(version='0.6.13',bridge_version='0.5.7',delivered_date='2026-11-06',coverage_snapshot_date='2026-10-22',identified_records=80523,qualified_records=74233,full_library_acceptance=False,program_delivery=True,bridge_sha256=hashes[0],paid_api_calls=0,save_loaded=False)
    (out/'delivery.json').write_text(json.dumps(delivery,indent=2),encoding='utf-8')
    (out/'\u4ea4\u4ed8\u72b6\u6001.md').write_text('# 2026-11-06 \u672c\u5730\u4ea4\u4ed8\n\n\u7a0b\u5e8f\u4ea4\u4ed8\u68c0\u67e5\u901a\u8fc7\u3002\u57fa\u4e8e 2026-10-22 \u7684 122 mod \u9694\u79bb\u526f\u672c\uff0c\u89c4\u5219\u5408\u683c\u8ba1\u6570 74233/80523\uff0892.19%\uff09\uff0c\u5c1a\u672a\u8fbe\u5230\u5168\u5e93\u57fa\u672c\u6c49\u5316\u9a8c\u6536\u30022026-11-06 \u5df2\u91cd\u65b0\u8fd0\u884c\u771f\u5b9e\u6846\u67b6\u53ca\u4e09\u4e2a mod \u5df2\u77e5\u6807\u7b7e\u63a2\u9488\uff0c\u5747\u901a\u8fc7\uff1b\u5b8c\u6574\u89c6\u89c9\u5e03\u5c40\u548c\u5b58\u6863\u5267\u60c5\u672a\u9a8c\u8bc1\u3002\n\n\u9000\u51fa\u6e38\u620f\u540e\u542f\u52a8 ModLingo/ModLingo.exe\uff0c\u9ed8\u8ba4\u590d\u7528\u7f13\u5b58\u3002\u77ff\u8f66\u672a\u77e5\u6807\u8bc6\u9700\u8981\u540d\u79f0\u5bf9\u5e94\u89c4\u5219\uff0c\u53cd\u590d\u8bf7\u6c42 API \u4e0d\u80fd\u89e3\u51b3\u3002\u79cd\u5b50\u9009\u9879\u7f16\u53f7\u95ee\u9898\u89c1\u5148\u8bfb\u6211.md\u3002\u672c\u5305\u6ca1\u6709\u66ff\u6362\u73a9\u5bb6\u6e38\u620f\u76ee\u5f55\u6216\u7f13\u5b58\u3002\n',encoding='utf-8')
    files=sorted(p for p in out.rglob('*') if p.is_file() and p.name!='SHA256SUMS.txt')
    (out/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(out).as_posix()+'\n' for p in files),encoding='utf-8')
    archive=Path(str(out)+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in out.rglob('*'):
            if p.is_file():z.write(p,out.name+'/'+p.relative_to(out).as_posix())
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None
    Path(str(archive)+'.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n',encoding='utf-8')
    print(json.dumps(dict(files=len(files),archive=str(archive),verified=True)))


if __name__=='__main__':main()
