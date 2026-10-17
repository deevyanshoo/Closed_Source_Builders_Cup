"""Public asset compression must preserve representation and cache headers."""
import gzip

from fastapi.testclient import TestClient

from procure.demo.app import create_app


def test_large_public_assets_gzip_exact_bytes_and_keep_security_headers(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    samples = {'main.js': b'const value = "copper cathode";\n' * 5000,
               'main.css': b'.panel { color: #123456; }\n' * 5000}
    for name, body in samples.items():
        (assets / name).write_bytes(body)
    with TestClient(create_app(web_dir=tmp_path), base_url='https://demo.test') as client:
        for name, original in samples.items():
            url = '/assets/' + name
            with client.stream('GET', url, headers={'Accept-Encoding': 'gzip'}) as response:
                wire = b''.join(response.iter_raw())
            assert response.status_code == 200
            assert response.headers['content-encoding'] == 'gzip'
            assert gzip.decompress(wire) == original
            assert len(wire) < len(original)
            assert response.headers.get('content-length') in (None, str(len(wire)))
            assert 'accept-encoding' in response.headers['vary'].lower()
            assert not response.headers.get('etag', '').startswith('"')
            assert response.headers['cache-control'] == 'no-store'
            assert response.headers['content-security-policy']
            identity = client.get(url, headers={'Accept-Encoding': 'identity'})
            assert identity.content == original
            assert 'content-encoding' not in identity.headers
            assert 'accept-encoding' in identity.headers['vary'].lower()


def test_asset_head_conditional_range_and_tiny_are_header_truthful(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    large = b'console.log("asset");\n' * 500
    (assets / 'large.js').write_bytes(large)
    (assets / 'tiny.js').write_bytes(b'x')
    with TestClient(create_app(web_dir=tmp_path), base_url='https://demo.test') as client:
        url = '/assets/large.js'
        head = client.head(url, headers={'Accept-Encoding': 'gzip'})
        assert head.status_code == 200 and head.content == b''
        assert head.headers.get('content-encoding') != 'gzip'
        assert head.headers['content-length'] == str(len(large))
        assert 'accept-encoding' in head.headers['vary'].lower()
        ranged = client.get(url, headers={'Accept-Encoding': 'gzip', 'Range': 'bytes=0-9'})
        assert ranged.status_code == 206 and ranged.content == large[:10]
        assert ranged.headers.get('content-encoding') != 'gzip'
        assert ranged.headers['content-length'] == '10'
        assert 'accept-encoding' in ranged.headers['vary'].lower()
        since = client.get(url, headers={'Accept-Encoding': 'gzip',
                                        'If-Modified-Since': head.headers['last-modified']})
        assert since.status_code == 304 and since.content == b''
        assert since.headers.get('content-encoding') != 'gzip'
        assert 'content-length' not in since.headers
        assert 'accept-encoding' in since.headers['vary'].lower()
        tiny = client.get('/assets/tiny.js', headers={'Accept-Encoding': 'gzip'})
        assert tiny.status_code == 200 and tiny.content == b'x'
        assert tiny.headers.get('content-encoding') != 'gzip'
        assert 'accept-encoding' in tiny.headers['vary'].lower()
        api = client.get('/api/demo/session', headers={'Accept-Encoding': 'gzip'})
        assert api.status_code == 200 and api.headers.get('content-encoding') != 'gzip'
