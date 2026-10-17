"""Public recovery errors retain origin protection and full catalogue budgets."""
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

from procure.demo.app import create_app


def test_missing_session_live_socket_has_actionable_error_without_provider():
    with TestClient(create_app(), base_url='https://demo.test') as client:
        with client.websocket_connect('wss://demo.test/api/demo/voice/live', headers={'Origin': 'https://demo.test'}) as socket:
            error = socket.receive_json()
            assert error['type'] == 'error'
            assert error['code'] == 'session_expired'
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            assert closed.value.code == 1008


def test_foreign_origin_still_rejected_before_socket_acceptance():
    with TestClient(create_app(), base_url='https://demo.test') as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('wss://demo.test/api/demo/voice/live', headers={'Origin': 'https://foreign.test'}):
                pass
