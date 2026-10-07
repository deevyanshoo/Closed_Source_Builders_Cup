"""Synthetic RSA tokens through vendor validation; zero provider requests."""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt

from procure.service_auth import Caller, GoogleVerifier, TokenRejected, authorize, load_callers, validate_audience

AUD = 'https://python.synthetic.run.app'
CALLER = Caller('123456789012345678901', 'gateway@synthetic.iam.gserviceaccount.com')

@pytest.fixture(scope='module')
def signed():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic-test')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).sign(key, hashes.SHA256()))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    signer = crypt.RSASigner.from_string(pem, key_id='synthetic-key')
    requests = []
    class Response:
        status = 200
        data = json.dumps({'synthetic-key': cert.public_bytes(serialization.Encoding.PEM).decode()}).encode()
    def request(url, method='GET', **kwargs):
        assert url == 'https://www.googleapis.com/oauth2/v1/certs'
        requests.append(url)
        return Response()
    def token(**changes):
        t = int(time.time())
        claims = {'iss': 'https://accounts.google.com', 'aud': AUD, 'iat': t-1, 'exp': t+60,
                  'sub': CALLER.subject, 'email': CALLER.email, 'email_verified': True}
        claims.update(changes)
        return jwt.encode(signer, claims).decode()
    return token, GoogleVerifier(request=request), requests


def test_valid_signature_and_exact_allowed_machine(signed):
    token, verifier, calls = signed
    assert authorize(token(), AUD, (CALLER,), verifier=verifier) == CALLER
    assert calls

@pytest.mark.parametrize('changes', [
    {'aud': 'https://wrong.synthetic.run.app'}, {'iss': 'https://attacker.invalid'},
    {'sub': 'other'}, {'email': 'other@synthetic.iam.gserviceaccount.com'},
    {'email_verified': False}, {'email_verified': 'true'},
    {'exp': 1}, {'iat': 9999999999},
])
def test_validly_signed_but_wrong_claims_are_rejected(signed, changes):
    token, verifier, _ = signed
    with pytest.raises(TokenRejected):
        authorize(token(**changes), AUD, (CALLER,), verifier=verifier)


def test_tampered_signature_and_malformed_token_rejected(signed):
    token, verifier, _ = signed
    value = token(); parts = value.split('.')
    parts[2] = ('A' if parts[2][0] != 'A' else 'B') + parts[2][1:]
    for bad in ['.'.join(parts), 'not-a-token', '']:
        with pytest.raises(TokenRejected):
            authorize(bad, AUD, (CALLER,), verifier=verifier)

@pytest.mark.parametrize('value', ['', 'http://localhost:8018', 'https://user:pass@example.test',
                                  'https://example.test/path', 'https://example.test?x=1'])
def test_private_audience_requires_explicit_https_origin(value):
    with pytest.raises(ValueError):
        validate_audience(value)


def test_caller_file_is_restricted_exact_and_unambiguous(tmp_path):
    p = tmp_path / 'callers.json'
    p.write_text(json.dumps({'callers': [{'subject': CALLER.subject, 'email': CALLER.email}]}))
    p.chmod(0o600)
    assert load_callers(p) == (CALLER,)
    p.chmod(0o644)
    with pytest.raises(ValueError): load_callers(p)
    p.chmod(0o600)
    p.write_text(json.dumps({'callers': [{'subject': CALLER.subject, 'email': CALLER.email}]*2}))
    with pytest.raises(ValueError): load_callers(p)
    p.write_text(json.dumps({'callers': [{'subject': CALLER.subject, 'email': 'human@example.test'}]}))
    with pytest.raises(ValueError): load_callers(p)
