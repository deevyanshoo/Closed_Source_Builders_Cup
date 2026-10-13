"""The deployed source package must extract without developer configuration files."""
import os
from pathlib import Path
import subprocess
import sys


def test_packaged_quote_extraction_uses_only_packaged_configuration(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    destination = tmp_path / 'package'
    prepared = subprocess.run([sys.executable, str(repo / 'deploy/judge-demo/prepare_package.py'),
                               str(destination)], capture_output=True, text=True)
    assert prepared.returncode == 0, prepared.stderr
    env = {**os.environ, 'OFFLINE': '1', 'RUN_LIVE_GEMINI': '0', 'MAX_LIVE_CALLS': '0',
           'PROCURE_CONFIG_DIR': str(destination / 'procurement-engine/config'),
           'PROCURE_DATA_DIR': str(tmp_path / 'data'),
           'PYTHONPATH': str(destination / 'procurement-engine/src') + os.pathsep +
                         os.environ.get('PYTHONPATH', '')}
    code = ('from datetime import date; from procure.demo.quote_service import extract_fixture; '
            'p=extract_fixture("late-copper",mode="rules",as_of=date(2026,10,14)); '
            'print(p["normalized"]["origin_country_code"])')
    extracted = subprocess.run([sys.executable, '-c', code], env=env, cwd=destination,
                               capture_output=True, text=True)
    assert extracted.returncode == 0, extracted.stderr
    assert extracted.stdout.strip() == 'CL'
