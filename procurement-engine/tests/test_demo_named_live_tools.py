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
            'review_sample_quotes','compare_suppliers','prepare_simulated_email',
            'navigate_workspace','operate_workspace'}==names
