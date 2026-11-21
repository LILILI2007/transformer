"""Check release correspondence and hash only explicitly public assets."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from modlingo import __version__
from modlingo.resource_rules import HASH


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--acceptance',type=Path,required=True)
    parser.add_argument('--commit',default='')
    args=parser.parse_args()
    release=ROOT/'release'
    acceptance=json.loads(args.acceptance.read_text(encoding='utf-8-sig'))
    if not acceptance.get('passed') or acceptance.get('version')!=__version__ or acceptance.get('paid_model_calls')!=0:
        raise ValueError('Acceptance failed or belongs to another build')
    if not all(acceptance.get(key) for key in ('gui_smoke_passed','runtime_without_dev_path','fresh_application_state')):
        raise ValueError('Portable runtime was not independently exercised')
    if acceptance.get('external_windows_verified') or acceptance.get('game_display_verified'):
        raise ValueError('Automatic fixtures cannot claim external game acceptance')
    package=ROOT/'dist/ModLingo'
    own_bridge=(ROOT/'bridge/package/ModLingo.Bridge.dll').read_bytes()
    embedded_bridge=(package/'_internal/bridge/package/ModLingo.Bridge.dll').read_bytes()
    rules=(ROOT/'modlingo/resource_rules.json').read_bytes()
    if embedded_bridge!=own_bridge or rules not in own_bridge:
        raise ValueError('Bridge and rule payload do not correspond')
    if (package/'_internal/modlingo/resource_rules.json').read_bytes()!=rules:
        raise ValueError('Python rule payload does not correspond')
    bridge_version=json.loads((package/'_internal/bridge/package/manifest.json').read_text())['Version']
    if bridge_version!=json.loads((ROOT/'bridge/manifest.json').read_text())['Version']:
        raise ValueError('Bridge manifest mismatch')
    forbidden={'captures.json','settings.json','translations.sqlite3','budget.sqlite3','API.txt'}
    assets=[]
    for name in ('ModLingo-Windows-x64.zip','ModLingo-source.zip','build-environment.txt'):
        path=release/name
        if path.suffix=='.zip':
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:raise ValueError('Corrupt archive')
                if any(Path(n).name in forbidden for n in archive.namelist()):raise ValueError('Private file in archive')
                if name=='ModLingo-source.zip':
                    if archive.read('ModLingo/modlingo/__init__.py')!=(ROOT/'modlingo/__init__.py').read_bytes():
                        raise ValueError('Source version mismatch')
                    if archive.read('ModLingo/bridge/package/ModLingo.Bridge.dll')!=own_bridge:
                        raise ValueError('Source Bridge mismatch')
        assets.append({'name':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
    (release/'portable-acceptance.json').write_text(json.dumps(acceptance,indent=2),encoding='utf-8')
    commit=args.commit or subprocess.check_output(['git','rev-parse','HEAD'],cwd=str(ROOT),text=True).strip()
    manifest={'version':__version__,'bridge_version':bridge_version,'source_commit':commit,
        'channel':'beta','rules_sha256':HASH,'assets':assets,'paid_model_calls':0,
        'external_windows_verified':False,'game_display_verified':False}
    (release/'release-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    names=[row['name'] for row in assets]+['portable-acceptance.json','release-manifest.json']
    (release/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256((release/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in names),encoding='ascii')
    print(json.dumps({'version':__version__,'bridge_version':bridge_version,'verified_assets':len(names),'passed':True}))


if __name__=='__main__':main()
