"""Wire-level negotiation keeps JSON complete and streaming/range traffic untouched."""
import asyncio
import gzip
import json

import pytest
from starlette.responses import JSONResponse, Response, StreamingResponse

from web.list_compression import ListCompressionMiddleware


def exchange(path='/api/video/scripts', *, encoding='gzip', method='GET', headers=(), response=None):
    messages = []
    body = [{'id': f's{i}', 'version': i, 'title': '同一标题' * 100} for i in range(30)]

    async def app(scope, receive, send):
        await (response if response is not None else JSONResponse(body))(scope, receive, send)

    async def receive():
        return {'type': 'http.disconnect'}

    async def send(message):
        messages.append(message)

    scope = {'type': 'http', 'method': method, 'path': path, 'scheme': 'http', 'query_string': b'',
             'headers': [(b'accept-encoding', encoding.encode()), *headers], 'http_version': '1.1',
             'asgi': {'version': '3.0', 'spec_version': '2.4'}}
    asyncio.run(ListCompressionMiddleware(app)(scope, receive, send))
    start = next(m for m in messages if m['type'] == 'http.response.start')
    return dict(start['headers']), b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body'), body, messages


@pytest.mark.parametrize('path', ['/api/video/scripts', '/api/video/shipments', '/api/video/autopilot'])
def test_lists_negotiate_gzip_without_dropping_rows_or_versions(path):
    headers, wire, original, _ = exchange(path)
    assert headers[b'content-encoding'] == b'gzip'
    assert b'Accept-Encoding' in headers[b'vary']
    decoded = gzip.decompress(wire)
    assert json.loads(decoded) == original
    assert int(headers[b'content-length']) == len(wire) < len(decoded) // 5


@pytest.mark.parametrize('encoding', ['', 'br', 'gzip;q=0', 'gzip;q=0.0, br', 'gzip;q=invalid'])
def test_clients_that_do_not_accept_gzip_receive_original_json(encoding):
    headers, wire, original, _ = exchange(encoding=encoding)
    assert b'content-encoding' not in headers
    assert json.loads(wire) == original


@pytest.mark.parametrize('encoding', ['gzip; q=0.5', 'br, GZIP;q=1'])
def test_positive_quality_values_are_supported(encoding):
    headers, wire, original, _ = exchange(encoding=encoding)
    assert headers[b'content-encoding'] == b'gzip'
    assert json.loads(gzip.decompress(wire)) == original


@pytest.mark.parametrize('path,method,headers', [('/api/video/scripts/detail', 'GET', ()),
    ('/api/video/scripts', 'POST', ()), ('/api/video/scripts', 'GET', ((b'range', b'bytes=0-99'),))])
def test_details_mutations_and_range_requests_are_not_compressed(path, method, headers):
    head, wire, original, _ = exchange(path, method=method, headers=headers)
    assert b'content-encoding' not in head
    assert json.loads(wire) == original


def test_small_json_and_media_ranges_stay_unchanged():
    headers, wire, _, _ = exchange(response=JSONResponse([]))
    assert b'content-encoding' not in headers and wire == b'[]'
    headers, wire, _, messages = exchange('/api/video/renders/r/media',
        response=Response(b'actual media bytes', status_code=206, media_type='video/mp4',
                          headers={'Content-Range': 'bytes 0-17/10000'}))
    assert messages[0]['status'] == 206
    assert headers[b'content-range'] == b'bytes 0-17/10000'
    assert b'content-encoding' not in headers and wire == b'actual media bytes'


def test_sse_retains_individual_chunks_without_compression():
    async def chunks():
        yield b'data: first\n\n'
        yield b'data: second\n\n'
    headers, wire, _, messages = exchange('/api/video/scripts/s/stream',
        response=StreamingResponse(chunks(), media_type='text/event-stream'))
    assert b'content-encoding' not in headers
    bodies = [m['body'] for m in messages if m['type'] == 'http.response.body' and m.get('body')]
    assert bodies == [b'data: first\n\n', b'data: second\n\n']
    assert wire == b'data: first\n\ndata: second\n\n'
