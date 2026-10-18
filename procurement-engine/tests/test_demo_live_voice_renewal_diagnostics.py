"""Renewal diagnostics identify transport failures without provider payloads."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS

from google.genai.errors import APIError
from procure.demo import live_voice
from test_demo_live_voice import Provider, Socket, next_type, setup, start
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close


def _events(caplog):
    return [json.loads(record.message) for record in caplog.records
            if record.name == 'procure.demo' and 'voice_natural_renewal' in record.message]


def test_diagnostic_accepts_only_static_source_and_integer_provider_code(caplog):
    secret = 'SECRET_PROVIDER_REASON_AND_SESSION'

    class UnsafeError(Exception):
        def __str__(self):
            raise AssertionError('exception string must not be read')

        def __repr__(self):
            raise AssertionError('exception repr must not be read')

    error = UnsafeError(secret)
    error.rcvd = NS(code=1011, reason=secret)
    error.code = 503
    error.status = secret
    with caplog.at_level(logging.INFO, logger='procure.demo'):
        live_voice._log_natural_renewal('read_provider', 2, error)
    assert _events(caplog) == [{'event': 'voice_natural_renewal', 'source': 'read_provider',
                                'event_count': 2, 'exception_type': 'UnsafeError',
                                'close_code': 1011}]
    assert secret not in caplog.text

    caplog.clear()
    error.rcvd.code = True
    error.code = '503 SECRET'
    with caplog.at_level(logging.INFO, logger='procure.demo'):
        live_voice._log_natural_renewal('user supplied ' + secret, 999, error)
    assert _events(caplog) == []
    with caplog.at_level(logging.INFO, logger='procure.demo'):
        live_voice._log_natural_renewal('read_provider', 3, error)
    assert _events(caplog) == [{'event': 'voice_natural_renewal', 'source': 'read_provider',
                                'event_count': 3, 'exception_type': 'UnsafeError'}]
    assert secret not in caplog.text


def test_real_provider_exception_shapes_log_only_numeric_codes(caplog):
    with caplog.at_level(logging.INFO, logger='procure.demo'):
        live_voice._log_natural_renewal('read_provider', 1,
            ConnectionClosedError(Close(1011, 'SECRET_CLOSE_REASON'), None))
        live_voice._log_natural_renewal('handshake_connect', 2,
            APIError(429, {'error': {'code': 429, 'message': 'SECRET_API_MESSAGE'}}))
    assert _events(caplog) == [
        {'event': 'voice_natural_renewal', 'source': 'read_provider', 'event_count': 1,
         'exception_type': 'ConnectionClosedError', 'close_code': 1011},
        {'event': 'voice_natural_renewal', 'source': 'handshake_connect', 'event_count': 2,
         'exception_type': 'APIError', 'status_code': 429},
    ]
    assert 'SECRET_' not in caplog.text


def test_go_away_and_empty_iterator_have_distinct_sources(caplog):
    class EmptyProvider(Provider):
        async def receive(self):
            if False:
                yield None

    async def scenario(first_provider):
        store, session = setup()
        second = Provider()
        calls = 0

        @asynccontextmanager
        async def factory():
            nonlocal calls
            calls += 1
            yield first_provider if calls == 1 else second

        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            client_factory=factory))
        await next_type(socket, 'ready')
        await next_type(socket, 'connection_status')
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)

    first = Provider()
    first.events.put_nowait(NS(go_away=NS(time_left='SECRET_PROVIDER_REASON_AND_SESSION')))
    with caplog.at_level(logging.INFO, logger='procure.demo'):
        asyncio.run(scenario(first))
    assert [event['source'] for event in _events(caplog)] == ['go_away']
    assert 'SECRET_PROVIDER_REASON_AND_SESSION' not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO, logger='procure.demo'):
        asyncio.run(scenario(EmptyProvider()))
    assert [event['source'] for event in _events(caplog)] == ['provider_receive_end']


def test_provider_task_and_handshake_failures_have_distinct_sources(caplog):
    class FailingProvider(Provider):
        async def receive(self):
            raise ValueError('SECRET_PROVIDER_REASON_AND_SESSION')
            yield None

    async def scenario(fail_connect):
        store, session = setup()
        calls = 0

        @asynccontextmanager
        async def factory():
            nonlocal calls
            calls += 1
            if calls == 1 and fail_connect:
                raise OSError('SECRET_PROVIDER_REASON_AND_SESSION')
            yield FailingProvider() if calls == 1 else Provider()

        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            client_factory=factory))
        await next_type(socket, 'ready')
        if not fail_connect:
            await next_type(socket, 'connection_status')
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)

    with caplog.at_level(logging.INFO, logger='procure.demo'):
        asyncio.run(scenario(False))
    assert [event['source'] for event in _events(caplog)] == ['read_provider']
    assert _events(caplog)[0]['exception_type'] == 'ValueError'
    assert 'SECRET_PROVIDER_REASON_AND_SESSION' not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO, logger='procure.demo'):
        asyncio.run(scenario(True))
    assert [event['source'] for event in _events(caplog)] == ['handshake_connect']
    assert _events(caplog)[0]['exception_type'] == 'OSError'
    assert 'SECRET_PROVIDER_REASON_AND_SESSION' not in caplog.text
