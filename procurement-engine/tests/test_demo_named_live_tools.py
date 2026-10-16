import asyncio
from types import SimpleNamespace as NS
from test_demo_live_voice import Socket, Provider, setup, start, connection, next_type
from procure.demo import live_voice


def test_named_form_action_updates_browser_without_exposing_session_to_model():
    async def scenario():
        store, session = setup()
        provider, socket = Provider(), Socket(start(session))
        calls = []
        async def tool(name, args, version, key):
            calls.append((name, args, key))
            return {'turn_id':'action-1','company_id':session.company_id,'context_version':1,
                    'status':'completed','assistant_text':'Buyer form updated.','agent_steps':[],
                    'evidence_ids':[],'proposal':None,'read_only':False,
                    'session':{'session_id':session.session_id,'csrf_token':'private-token',
                               'company_id':session.company_id,'context_version':1},
                    'action_result':{'tool':'fill_form','summary':'Buyer form updated.'}}
        task = asyncio.create_task(live_voice.run_live_voice(socket,session=session,store=store,
                    operate_workspace=lambda *_:None,workspace_tools=tool,client_factory=connection(provider)))
        await next_type(socket,'ready')
        event = NS(tool_call=NS(function_calls=[NS(name='fill_buyer_form',id='fill-1',
                            args={'material_id':'copper_cathode','values':{'quantity':'1200'}})]),server_content=None)
        await provider.events.put(event)
        result = (await next_type(socket,'tool_result'))['result']
        await next_type(socket,'turn_complete')
        assert result['session']['csrf_token']=='private-token'
        assert 'session' not in provider.tool_response.response
        assert 'private-token' not in str(provider.tool_response.response)
        await provider.events.put(event)
        await next_type(socket,'tool_result')
        await next_type(socket,'turn_complete')
        assert len(calls)==2 and calls[0] == calls[1]
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task,2)
    asyncio.run(scenario())


def test_named_tools_declared_with_bounded_arguments():
    names={d.name for d in live_voice._config().tools[0].function_declarations}
    assert {'read_workspace','analyze_inventory','fill_buyer_form','load_sample_quotes',
            'review_sample_quotes','compare_suppliers','prepare_rfq','prepare_simulated_email',
            'navigate_workspace','operate_workspace','read_risks','read_news',
            'read_carbon','read_routes','estimate_carbon','read_shipments','preview_supplier_delay',
            'prepare_research','confirm_research','confirm_workspace_action','read_research'}==names
    assert not any(('confirm' in name or 'approve' in name) and name not in
                   {'confirm_research', 'confirm_workspace_action'}
                   for name in names)


def test_live_rejects_old_direct_recovery_tool_without_workspace_call():
    async def scenario():
        store, session = setup()
        provider, socket = Provider(), Socket(start(session))
        calls = []
        async def tool(*args):
            calls.append(args)
            raise AssertionError('hidden action tool reached workspace')
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session, store=store,
            operate_workspace=tool, workspace_tools=tool, client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await provider.events.put(NS(tool_call=NS(function_calls=[NS(
            name='prepare_shipment_recovery', id='hidden-recovery',
            args={'shipment_id':'shipment-1', 'delay_days':7})]), server_content=None))
        error = await next_type(socket, 'error')
        assert error['code'] == 'voice_invalid' and calls == []
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


def test_live_config_binds_exact_owned_material_ids_without_session_credentials():
    from procure.demo.company import get_company
    config=live_voice._config(get_company('solara-cables'))
    tools={tool.name:tool for tool in config.tools[0].function_declarations}
    ids=tools['fill_buyer_form'].parameters_json_schema['properties']['material_id']['enum']
    assert ids==['copper','polymer','aluminium']
    assert 'decision_date' not in tools['fill_buyer_form'].parameters_json_schema['properties']['values']['properties']
    assert set(tools['review_sample_quotes'].parameters_json_schema['properties']) == {'material_id'}
    recipients = tools['prepare_simulated_email'].parameters_json_schema['properties']['recipient_label']['enum']
    assert 'first supplier' in recipients and any('Swift Copper' in name for name in recipients)
    assert 'TerraPack Films' not in str(config.system_instruction)
    assert 'Cable insulation polymer' in str(config.system_instruction)
    assert 'csrf_token' not in str(config.system_instruction)
    assert 'session_id' not in str(config.system_instruction)


def test_failed_tool_cannot_stream_a_false_success_claim():
    async def scenario():
        store,session=setup();socket,provider=Socket(start(session)),Provider()
        async def operate(*_):
            return {'turn_id':'failed','company_id':session.company_id,'context_version':1,
                'status':'needs_clarification','assistant_text':'No change was made. Review quotes first.',
                'agent_steps':[],'evidence_ids':[],'proposal':None,'read_only':True}
        task=asyncio.create_task(live_voice.run_live_voice(socket,session=session,store=store,
            operate_workspace=operate,client_factory=connection(provider)))
        await next_type(socket,'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await next_type(socket,'tool_result');await next_type(socket,'turn_complete')
        assert not any(e['type']=='audio' for e in socket.history)
        captions=[e['text'] for e in socket.history if e['type']=='transcript' and e['direction']=='output']
        assert captions==['No change was made. Review quotes first.']
        assert provider.tool_response.response['tool_succeeded'] is False
        await socket.frames.put('{"type":"stop"}');await asyncio.wait_for(task,2)
    asyncio.run(scenario())


def test_failure_suppression_resets_after_provider_interruption():
    class QuietProvider(Provider):
        async def send_tool_response(self,*,function_responses):
            self.tool_response=function_responses
    async def scenario():
        store,session=setup();socket,provider=Socket(start(session)),QuietProvider()
        async def operate(*_):
            return {'turn_id':'failed','company_id':session.company_id,'context_version':1,
                'status':'needs_clarification','assistant_text':'No change was made.',
                'agent_steps':[],'evidence_ids':[],'proposal':None,'read_only':True}
        task=asyncio.create_task(live_voice.run_live_voice(socket,session=session,store=store,
            operate_workspace=operate,client_factory=connection(provider)))
        await next_type(socket,'ready');await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await next_type(socket,'tool_result');await next_type(socket,'interrupted')
        await provider.events.put(NS(tool_call=None,server_content=NS(interrupted=True,
            output_transcription=NS(text='A stale success claim.'),
            model_turn=NS(parts=[NS(inline_data=NS(data=b'\x01\x00',mime_type='audio/pcm'))]),turn_complete=False)))
        await next_type(socket,'interrupted')
        await provider.events.put(NS(tool_call=None,server_content=NS(interrupted=False,
            output_transcription=NS(text='A new general answer.'),
            model_turn=NS(parts=[NS(inline_data=NS(data=b'\x02\x00',mime_type='audio/pcm'))]),turn_complete=True)))
        audio=await next_type(socket,'audio');assert audio['data']=='AgA='
        await next_type(socket,'turn_complete')
        assert not any(e.get('text')=='A stale success claim.' for e in socket.history)
        await socket.frames.put('{"type":"stop"}');await asyncio.wait_for(task,2)
    asyncio.run(scenario())


def test_browser_interrupt_does_not_release_stale_failed_speech_before_provider_ack():
    class QuietProvider(Provider):
        async def send_tool_response(self,*,function_responses):self.tool_response=function_responses
    async def scenario():
        store,session=setup();socket,provider=Socket(start(session)),QuietProvider()
        async def operate(*_):
            return {'turn_id':'failed','company_id':session.company_id,'context_version':1,
                'status':'needs_clarification','assistant_text':'No change was made.',
                'agent_steps':[],'evidence_ids':[],'proposal':None,'read_only':True}
        task=asyncio.create_task(live_voice.run_live_voice(socket,session=session,store=store,
            operate_workspace=operate,client_factory=connection(provider)))
        await next_type(socket,'ready');await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await next_type(socket,'tool_result');await next_type(socket,'interrupted')
        await socket.frames.put('{"type":"interrupt"}');await next_type(socket,'interrupted')
        await provider.events.put(NS(tool_call=None,server_content=NS(interrupted=False,
            output_transcription=NS(text='A stale success claim.'),model_turn=NS(parts=[NS(
            inline_data=NS(data=b'\x01\x00',mime_type='audio/pcm'))]),turn_complete=False)))
        await provider.events.put(NS(tool_call=None,server_content=NS(interrupted=True,model_turn=None,turn_complete=False)))
        await next_type(socket,'interrupted')
        assert not any(e['type']=='audio' or e.get('text')=='A stale success claim.' for e in socket.history)
        await provider.events.put(NS(tool_call=None,server_content=NS(interrupted=False,
            output_transcription=NS(text='A new answer.'),model_turn=NS(parts=[NS(
            inline_data=NS(data=b'\x02\x00',mime_type='audio/pcm'))]),turn_complete=True)))
        assert (await next_type(socket,'audio'))['data']=='AgA='
        await next_type(socket,'turn_complete');await socket.frames.put('{"type":"stop"}');await asyncio.wait_for(task,2)
    asyncio.run(scenario())


def test_interrupted_provider_audio_still_consumes_output_byte_bound(monkeypatch):
    monkeypatch.setattr(live_voice,'MAX_OUTPUT_BYTES',2)
    async def scenario():
        store,session=setup();socket,provider=Socket(start(session)),Provider()
        task=asyncio.create_task(live_voice.run_live_voice(socket,session=session,store=store,
            operate_workspace=lambda *_:None,client_factory=connection(provider)))
        await next_type(socket,'ready')
        await provider.events.put(NS(tool_call=None,server_content=NS(interrupted=True,
            model_turn=NS(parts=[NS(inline_data=NS(data=b'\x01\x00\x02\x00',mime_type='audio/pcm'))]),turn_complete=False)))
        assert (await next_type(socket,'error'))['code']=='voice_invalid'
        await asyncio.wait_for(task,2)
        assert not any(e['type']=='audio' for e in socket.history)
    asyncio.run(scenario())
