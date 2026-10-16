"""Authoritative live reads must block generation until their responses arrive."""
import json
from types import SimpleNamespace

from google.genai import _common, _live_converters, types

from procure.demo import live_voice


def test_pinned_vertex_live_setup_serializes_blocking_authoritative_reads():
    assert live_voice.MODEL == 'gemini-3.8-live'
    config = live_voice._config()
    params = types.LiveConnectParameters(model=live_voice.MODEL,
                                         config=config).model_dump(exclude_none=True)
    request = _common.convert_to_dict(_live_converters._LiveConnectParameters_to_vertex(
        SimpleNamespace(vertexai=True), params))
    request.pop('config')
    wire = json.loads(json.dumps(_common.encode_unserializable_types(request)))
    declarations = {item['name']: item for item in wire['setup']['tools'][0]['functionDeclarations']}

    assert declarations['read_carbon']['behavior'] == 'BLOCKING'
    assert declarations['read_shipments']['behavior'] == 'BLOCKING'
    assert 'behavior' not in declarations['prepare_shipment_recovery']
    assert 'behavior' not in declarations['read_news']
    assert len(declarations) == len(config.tools[0].function_declarations)
