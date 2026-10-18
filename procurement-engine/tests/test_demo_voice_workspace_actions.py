"""Native action approval is a fresh user audio event, never a model instruction."""
import asyncio
from datetime import datetime, timedelta, timezone

from procure.demo import live_voice
from procure.demo.sessions import SessionStore
from test_demo_voice_research_consent import Socket, Provider, connection, send_pcm, finish, turn


async def scenario(verifier):
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    store = SessionStore(clock=lambda: now)
    session = store.create()
    socket, provider = Socket(session), Provider()
    calls = []

    async def operate(message, version, key):
        calls.append(('operate_workspace', message))
        proposal = {'proposal_id': 'watch-1', 'kind': 'watch',
                    'summary': 'Review the watch.',
                    'parameters': {'material_id': 'copper', 'evidence_ids': []},
                    'plan_id': None, 'offer_versions': {}, 'context_version': 1,
                    'expires_at': (now + timedelta(minutes=5)).isoformat(),
                    '_session_id': session.session_id, '_company_id': session.company_id,
                    '_marker': 'watch-marker'}
        session.pending_proposal = proposal
        return turn(session, 'proposal', proposal={k: v for k, v in proposal.items()
                    if not k.startswith('_')})

    async def workspace(name, args, version, key):
        calls.append((name, args))
        return {'turn_id': 'applied-1', 'company_id': session.company_id,
                'context_version': 1, 'status': 'completed',
                'assistant_text': 'Applied the watch.', 'agent_steps': [],
                'evidence_ids': [], 'proposal': None, 'read_only': False,
                'session': {'session_id': session.session_id, 'company_id': session.company_id,
                            'context_version': 1},
                'action_result': {'tool': 'watch', 'summary': 'Applied.'}}

    task = asyncio.create_task(live_voice.run_live_voice(socket, session=session, store=store,
        operate_workspace=operate, workspace_tools=workspace,
        client_factory=lambda: connection(provider), verify_spoken_consent=verifier,
        research_tools=lambda *_: None))
    await socket.next('ready')
    await send_pcm(socket, provider)
    await provider.activity('ACTIVITY_START', '0.100s')
    await provider.spoken('input', 'Watch copper', finished=True)
    await provider.activity('ACTIVITY_END', '0.600s')
    await provider.call('operate_workspace', {'message': 'Watch copper'}, 'prepare-watch')
    assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
    response = await asyncio.wait_for(provider.responses.get(), 2)
    review = response.response['required_spoken_response']
    assert 'watch for copper' in review and 'confirm action' in review
    return store, session, socket, provider, calls, task, review


def test_native_action_denies_model_approval_before_full_spoken_review():
    async def check():
        _, _, socket, provider, calls, task, _ = await scenario(lambda _: None)
        await provider.call('confirm_workspace_action', {'proposal_id': 'watch-1'}, 'model-approval')
        assert (await socket.next('tool_result'))['result']['status'] == 'needs_clarification'
        assert [name for name, _ in calls] == ['operate_workspace']
        await finish(socket, task)
    asyncio.run(check())


def test_native_action_applies_once_after_full_audio_and_independent_confirmation():
    async def check():
        async def verify(_):
            return 'confirm action'
        _, _, socket, provider, calls, task, review = await scenario(verify)
        await provider.spoken('output', review, complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirm action', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '1.100s')
        await provider.activity('ACTIVITY_END', '1.600s')
        await provider.call('confirm_workspace_action', {'proposal_id': 'watch-1'}, 'apply-watch')
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert [name for name, _ in calls] == ['operate_workspace', 'confirm_workspace_action']
        await provider.call('confirm_workspace_action', {'proposal_id': 'watch-1'}, 'replay-watch')
        assert (await socket.next('tool_result'))['result']['status'] == 'needs_clarification'
        assert len(calls) == 2
        await finish(socket, task)
    asyncio.run(check())


def test_native_action_denies_partial_readback_wrong_id_and_changed_proposal():
    async def check(case):
        async def verify(_):
            return 'confirm action'
        store, session, socket, provider, calls, task, review = await scenario(verify)
        spoken = review if case != 'partial' else 'Say confirm action to apply.'
        await provider.spoken('output', spoken, complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirm action', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '1.100s')
        await provider.activity('ACTIVITY_END', '1.600s')
        if case == 'changed':
            session.pending_proposal['proposal_id'] = 'new-watch'
        if case == 'expired':
            store.clock = lambda: datetime(2026, 10, 15, 0, 6, tzinfo=timezone.utc)
        identifier = 'wrong-id' if case == 'wrong_id' else 'watch-1'
        await provider.call('confirm_workspace_action', {'proposal_id': identifier}, 'denied-' + case)
        assert (await socket.next('tool_result'))['result']['status'] == 'needs_clarification'
        assert [name for name, _ in calls] == ['operate_workspace']
        await finish(socket, task)
    for case in ('partial', 'wrong_id', 'changed', 'expired'):
        asyncio.run(check(case))


def test_native_action_denies_negated_audio_even_when_verifier_says_confirm():
    async def check():
        async def verify(_):
            return 'confirm action'
        _, _, socket, provider, calls, task, review = await scenario(verify)
        await provider.spoken('output', review, complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'No, do not confirm action', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '1.100s')
        await provider.activity('ACTIVITY_END', '1.600s')
        await provider.call('confirm_workspace_action', {'proposal_id': 'watch-1'}, 'negated')
        assert (await socket.next('tool_result'))['result']['status'] == 'needs_clarification'
        assert len(calls) == 1
        await finish(socket, task)
    asyncio.run(check())


def test_native_action_interrupt_disarms_review():
    async def check():
        async def verify(_):
            return 'confirm action'
        _, _, socket, provider, calls, task, review = await scenario(verify)
        await provider.spoken('output', review, complete=True)
        await socket.next('turn_complete')
        await socket.frames.put('{"type":"interrupt"}')
        await socket.next('interrupted')
        await provider.call('confirm_workspace_action', {'proposal_id': 'watch-1'}, 'after-interrupt')
        assert (await socket.next('tool_result'))['result']['status'] == 'needs_clarification'
        assert len(calls) == 1
        await finish(socket, task)
    asyncio.run(check())
