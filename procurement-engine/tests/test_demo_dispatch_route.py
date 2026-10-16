"""The hosted app serves the dispatch workspace from the same web build as /demo."""
from fastapi.testclient import TestClient

from procure.demo.app import create_app


def test_dispatch_route_returns_the_app_shell(tmp_path):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'index.html').write_text('<!doctype html><div id="root"></div>', encoding='utf-8')
    with TestClient(create_app(web_dir=tmp_path), base_url='https://demo.test') as client:
        for path in ('/dispatch', '/dispatch?from=demo', '/demo'):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.text == '<!doctype html><div id="root"></div>'
        assert client.get('/planning').status_code == 404
