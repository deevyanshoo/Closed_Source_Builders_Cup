"""The hosted matrix pins its target and keeps a recoverable, redacted receipt."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('all_material_workflow_smoke',
                                               HERE / 'all_material_workflow_smoke.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class PreflightClient:
    def __init__(self, *, revision='closed-source-quote-demo-00040-n4g',
                 persistence='firestore'):
        self.revision = revision
        self.persistence = persistence
        self.paths = []

    def json(self, stage, path):
        self.paths.append(path)
        if path == '/-/health':
            return {'ready': True, 'revision': self.revision,
                    'persistence': self.persistence}
        return {'companies': []}


@pytest.mark.parametrize('revision,persistence', [
    ('different-revision', 'firestore'),
    ('closed-source-quote-demo-00040-n4g', 'ephemeral'),
])
def test_wrong_revision_or_storage_stops_before_session(revision, persistence):
    client = PreflightClient(revision=revision, persistence=persistence)
    with pytest.raises(audit.AcceptanceError) as error:
        audit._run('https://example.test', client, {'scenarios': []},
                   lambda _receipt: None, 'closed-source-quote-demo-00040-n4g')
    assert error.value.stage == 'health'
    assert client.paths == ['/-/health']


def test_checkpoint_is_atomic_and_contains_only_supplied_redacted_state(tmp_path):
    output = tmp_path / 'receipt.json'
    output.write_text('{"state":"running"}\n')
    progress = {'state': 'running', 'deployed_revision': 'rev-1',
                'fixture_count': 1, 'scenario_count': 0,
                'in_flight': {'company_id': 'sample', 'material': 'copper',
                              'fixture_index': 2, 'step': 'extract_review_save'}}
    audit.checkpoint_file(output, progress)
    assert json.loads(output.read_text()) == progress
    assert not output.with_name(output.name + '.tmp').exists()
