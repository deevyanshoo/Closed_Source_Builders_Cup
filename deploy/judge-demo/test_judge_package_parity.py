"""Source/runtime packaging and static public share metadata parity."""
from pathlib import Path
import ast
import struct
ROOT=Path(__file__).resolve().parents[2]

def test_runtime_config_allowlist_covers_route_replay_hindi_carbon_and_news():
    tree=ast.parse((ROOT/'deploy/judge-demo/prepare_package.py').read_text())
    literals={node.value for node in ast.walk(tree) if isinstance(node,ast.Constant) and isinstance(node.value,str)}
    for name in ('route_display_v1.json','judge_suez_2021_v1.json','judge_hindi_case_v1.json','locations_v1.json','news_sources_v1.json','carbon_factors_v1.json'):
        assert name in literals, f'missing runtime package asset: {name}'
        assert (ROOT/'procurement-engine/config'/name).is_file()

def test_static_metadata_and_share_asset_have_public_absolute_urls():
    html=(ROOT/'apps/web/index.html').read_text()
    origin='https://zoorq-963802031195.asia-south1.run.app'
    for field in ('og:title','og:description','og:url','og:image','og:image:width','og:image:height','twitter:card','twitter:image'):
        assert field in html
    assert '<noscript>' in html and 'rel="canonical"' in html
    assert origin+'/assets/zoorq-share-card.png' in html
    assert 'session_id' not in html
    raw=(ROOT/'apps/web/public/assets/zoorq-share-card.png').read_bytes()
    assert raw[:8]==b'\x89PNG\r\n\x1a\n'
    assert struct.unpack('>II',raw[16:24])==(1200,630)

def test_actual_prepared_package_contains_runtime_config_and_public_share_image(tmp_path):
    import hashlib
    import json
    import subprocess
    import sys
    package=tmp_path/'judge-package'
    run=subprocess.run([sys.executable,str(ROOT/'deploy/judge-demo/prepare_package.py'),str(package)],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    manifest=json.loads((package/'source-manifest.json').read_text())
    records={row['path']:row for row in manifest['files']}
    assets=['procurement-engine/config/'+name for name in ('route_display_v1.json','judge_suez_2021_v1.json','judge_hindi_case_v1.json','locations_v1.json','news_sources_v1.json','carbon_factors_v1.json')]
    assets+=['apps/web/dist/assets/zoorq-share-card.png','apps/web/dist/index.html','procurement-engine/src/procure/demo/experience_service.py']
    for path in assets:
        assert path in records
        assert hashlib.sha256((package/path).read_bytes()).hexdigest()==records[path]['sha256']
    assert not any('.env' in path or 'node_modules' in path or '__pycache__' in path for path in records)
    assert 'og:image' in (package/'apps/web/dist/index.html').read_text()
    # Exercise the create-only package with the existing authorized dependency
    # path; the legacy package test overwrites PYTHONPATH and cannot see it.
    import os
    env=os.environ.copy()
    inherited=[str(Path(part).resolve()) for part in env.get('PYTHONPATH','').split(os.pathsep) if part]
    env.update(PYTHONPATH=os.pathsep.join([str(package/'procurement-engine/src'),*inherited]),
               PROCURE_CONFIG_DIR=str(package/'procurement-engine/config'),
               PROCURE_DATA_DIR=str(tmp_path/'data'),OFFLINE='1')
    code=('from datetime import date; '
          'from procure.demo.quote_service import extract_fixture; '
          'from procure.demo.company import list_companies; '
          'from procure.demo.route_display import build_route_display; '
          'from procure.demo.experience_service import fixture; '
          'p=extract_fixture("late-copper",mode="rules",as_of=date(2026,10,14)); '
          'assert p["normalized"]["origin_country_code"]=="CL"; '
          'assert fixture("judge_hindi_case_v1.json")["source_id"]=="pib-2267065"; '
          'assert fixture("judge_suez_2021_v1.json")["schema"]==1; '
          'assert len(list_companies())==10; '
          '[build_route_display(c,r["route_id"],r["materials"][0],0) for c in list_companies() for r in c["routes"]]')
    smoke=subprocess.run([sys.executable,'-c',code],env=env,cwd=package,capture_output=True,text=True)
    assert smoke.returncode==0,smoke.stderr
