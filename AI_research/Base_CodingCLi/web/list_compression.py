"""Compress large JSON lists without buffering narration streams or media ranges."""
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


LIST_PATHS = frozenset(('/api/video/scripts', '/api/video/shipments', '/api/video/autopilot'))


def accepts_gzip(value: str) -> bool:
    for entry in value.lower().split(','):
        coding, *parameters = entry.strip().split(';')
        if coding.strip() != 'gzip':
            continue
        quality = next((part.split('=', 1)[1].strip() for part in parameters
                        if part.strip().startswith('q=')), '1')
        try:
            return 0 < float(quality) <= 1
        except ValueError:
            return False
    return False


class ListCompressionMiddleware:
    """Keep existing JSON contracts and negotiate compression only on list GETs."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=1024, compresslevel=3)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http' and scope['method'] == 'GET' and scope['path'].rstrip('/') in LIST_PATHS:
            headers = Headers(scope=scope)
            if 'range' not in headers and accepts_gzip(headers.get('accept-encoding', '')):
                # Content-coding names are case insensitive; Starlette's gzip
                # negotiation uses a case-sensitive substring check.
                scope = {**scope, 'headers': [(name, value.lower() if name == b'accept-encoding' else value)
                                             for name, value in scope['headers']]}
                await self.compressed(scope, receive, send)
                return
        await self.app(scope, receive, send)
