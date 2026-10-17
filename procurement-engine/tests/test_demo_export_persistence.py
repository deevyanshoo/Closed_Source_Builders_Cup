"""Downloaded evidence reports the workspace's actual persistence contract."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from procure.demo.app import create_app
from procure.demo.session_persistence import MemorySessionPersistence


@pytest.mark.parametrize('durable', [False, True])
def test_export_persistence_matches_session_and_health(durable):
    app = create_app(clock=lambda: datetime(2026, 10, 15, tzinfo=timezone.utc),
                     session_backend=MemorySessionPersistence() if durable else None)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        health = client.get('/-/health').json()
        response = client.get('/api/demo/export')
        assert response.status_code == 200
        assert 'attachment' in response.headers['content-disposition']
        exported = response.json()
        assert exported['persistence'] == session['persistence'] == health['persistence']
        assert exported['persistence'] == ('firestore' if durable else 'ephemeral')
        assert exported['company']['company_id'] == session['company_id']
        assert exported['offers'] == session['offers']
