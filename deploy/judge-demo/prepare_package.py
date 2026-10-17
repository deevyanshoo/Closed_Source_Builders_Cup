"""Prepare an explicit source allowlist for Cloud Build; never package private data."""
from pathlib import Path
import hashlib
import json
import shutil
import sys

repo = Path(__file__).resolve().parents[2]
destination = Path(sys.argv[1]).resolve()
if destination.exists():
    raise SystemExit('Create-only package destination already exists')
destination.mkdir(parents=True)
ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.env', '*.log')
for source in ['procurement-engine/src', 'procurement-engine/demo_fixtures', 'apps/web/dist']:
    shutil.copytree(repo / source, destination / source, ignore=ignore)
config = destination / 'procurement-engine/config'
config.mkdir(parents=True)
for name in ['materials.yaml', 'assumptions.yaml', 'iso3166_alpha2.csv', 'carbon_factors_v1.json',
             'route_display_v1.json', 'judge_suez_2021_v1.json', 'judge_hindi_case_v1.json',
             'locations_v1.json', 'news_sources_v1.json']:
    shutil.copy2(repo / 'procurement-engine/config' / name, config / name)
deploy = destination / 'deploy/judge-demo'
deploy.mkdir(parents=True)
shutil.copy2(repo / 'deploy/judge-demo/requirements.txt', deploy / 'requirements.txt')
shutil.copy2(repo / 'deploy/judge-demo/Dockerfile', destination / 'Dockerfile')
records = []
for item in sorted(destination.rglob('*')):
    if item.is_file():
        records.append({'path': item.relative_to(destination).as_posix(),
                        'bytes': item.stat().st_size,
                        'sha256': hashlib.sha256(item.read_bytes()).hexdigest()})
raw = json.dumps(records, sort_keys=True, separators=(',', ':')).encode()
manifest = {'files': records, 'file_count': len(records), 'bytes': sum(r['bytes'] for r in records),
            'source_bundle_sha256': hashlib.sha256(raw).hexdigest()}
(destination / 'source-manifest.json').write_text(json.dumps(manifest, indent=2))
print(json.dumps({key: value for key, value in manifest.items() if key != 'files'}, indent=2))
