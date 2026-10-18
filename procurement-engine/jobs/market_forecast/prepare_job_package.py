"""Create-only remote build context; never upload repository/private files."""
from pathlib import Path
import hashlib
import json
import shutil
import sys

root = Path(__file__).resolve().parents[3]
source = root / 'procurement-engine/jobs/market_forecast'
target = Path(sys.argv[1]).resolve()
if target.exists():
    raise SystemExit('Create-only job package destination already exists')
target.mkdir(parents=True)
ignore = shutil.ignore_patterns('__pycache__','*.pyc','.env','*.log')
for relative in ('procure/__init__.py', 'procure/demo/__init__.py',
                 'procure/demo/market_snapshot_store.py'):
    destination = target / 'procurement-engine/src' / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / 'procurement-engine/src' / relative, destination)
job = target / 'procurement-engine/jobs/market_forecast'
job.mkdir(parents=True)
for name in ('Dockerfile','runner.py','requirements.txt','reference_snapshot.json'):
    shutil.copy2(source / name, job / name)
shutil.copytree(source / 'bundle', job / 'bundle', ignore=ignore)
shutil.copy2(source / 'cloudbuild.yaml', target / 'cloudbuild.yaml')
records = [{'path': p.relative_to(target).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
           for p in sorted(target.rglob('*')) if p.is_file()]
(target / 'source-manifest.json').write_text(json.dumps({'files': records}, indent=2))
print(json.dumps({'file_count': len(records), 'manifest_path': str(target / 'source-manifest.json')}))
