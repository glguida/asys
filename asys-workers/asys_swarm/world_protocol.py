"""Versioned JSON world operations carried by ordinary Runtime channels.

The channel has one swarm consumer and one world service. Operations are pure:
all simulation state is supplied in the request and returned in the response.
Re-executing an interrupted operation must produce the same result.
"""
from jsonschema import Draft7Validator

from asys_runtime.files import validate_name

from .config import json_value, object_fields
from .limits import ARTIFACT_BYTES, STEP_BYTES, WORLD_BYTES


VERSION = 1
WIRE_BYTES = 7 * 1024 * 1024
CALLBACKS = ('initialize', 'observe', 'step', 'evaluate', 'artifacts', 'action_schema')
ARITY = {'describe': 0, 'initialize': 3, 'observe': 2, 'step': 2,
         'evaluate': 2, 'artifacts': 1, 'action_schema': 0}


def identity(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or '\0' in value:
        raise ValueError('World implementation identity must be nonempty text of at most 256 characters')
    return value


def local_schema(schema):
    if isinstance(schema, dict):
        if '$ref' in schema and (not isinstance(schema['$ref'], str) or not schema['$ref'].startswith('#')):
            raise ValueError('Action schema references must be local fragments')
        for value in schema.values():
            local_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            local_schema(value)


def action_schema(value):
    value = json_value(value, limit=128 * 1024)
    if not isinstance(value, dict):
        raise ValueError('action_schema() must return a JSON Schema object')
    local_schema(value)
    Draft7Validator.check_schema(value)
    return value


def arguments(method, values):
    if method not in ARITY:
        raise ValueError('Unknown world operation')
    if not isinstance(values, (list, tuple)) or len(values) != ARITY[method]:
        raise ValueError(f'{method} requires {ARITY[method]} arguments')
    copied = []
    for index, value in enumerate(values):
        if method in {'observe', 'step', 'evaluate', 'artifacts'} and index == 0:
            copied.append(json_value(value, limit=WORLD_BYTES))
        elif method == 'step' and index == 1:
            copied.append(json_value(value, limit=STEP_BYTES))
        else:
            copied.append(json_value(value))
    return copied


def result(method, value):
    if method == 'initialize':
        return json_value(value, limit=WORLD_BYTES)
    if method == 'step':
        value = json_value(value, limit=STEP_BYTES)
        if isinstance(value, dict) and 'state' in value:
            json_value(value['state'], limit=WORLD_BYTES)
        return value
    if method == 'artifacts':
        envelope = {'outbox': [{'data': {'output': {'artifacts': value}}}]}
        return json_value(envelope, limit=ARTIFACT_BYTES)['outbox'][0]['data']['output']['artifacts']
    if method == 'action_schema':
        return action_schema(value)
    return json_value(value)


def correlation(value):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != VERSION:
        raise ValueError('Unsupported world protocol version')
    validate_name('world request ID', value.get('requestId'))
    validate_name('world run ID', value.get('runId'))
    return value['runId'], value['requestId']


def request(value):
    value = json_value(value, limit=WIRE_BYTES)
    object_fields(value, 'world request', {'version', 'requestId', 'runId', 'method', 'arguments', 'identity'})
    correlation(value)
    value['arguments'] = arguments(value.get('method'), value.get('arguments'))
    if value['method'] == 'describe':
        if value.get('identity') is not None:
            raise ValueError('describe must not assume an implementation identity')
    else:
        identity(value.get('identity'))
    return value


def response(value):
    value = json_value(value, limit=WIRE_BYTES)
    object_fields(value, 'world response', {'version', 'requestId', 'runId', 'method', 'identity', 'ok', 'result', 'error'})
    correlation(value)
    identity(value.get('identity'))
    if not isinstance(value.get('method'), str) or len(value['method']) > 64:
        raise ValueError('World response requires an operation name')
    if type(value.get('ok')) is not bool:
        raise ValueError('World response requires boolean ok')
    if value['ok']:
        if 'result' not in value or 'error' in value:
            raise ValueError('Successful world response requires only result')
    else:
        if 'result' in value:
            raise ValueError('Failed world response must not contain result')
        error = value.get('error')
        object_fields(error, 'world error', {'code', 'message'})
        if (not isinstance(error.get('code'), str) or not isinstance(error.get('message'), str)
                or len(error['code']) > 64 or len(error['message']) > 2000):
            raise ValueError('Invalid world error')
    return value


def send(writer, event_type, value):
    # Include Runtime's indentation depth when bounding the final event file.
    json_value({'version': 1, 'sequence': 999999999, 'type': event_type,
                'time': '2000-01-01T00:00:00.000000Z', 'data': value}, limit=WIRE_BYTES)
    return writer.send(event_type, value)
