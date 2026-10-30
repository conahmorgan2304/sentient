"""Bounded reads for streamed Frigate media responses."""

from aiohttp import ClientResponse


class UnsupportedCodec(ValueError):
    """The downloaded file contains a codec explicitly refused by the caller."""


async def read_media(response: ClientResponse, max_bytes: int) -> bytes | None:
    """Read through EOF without accepting empty or oversized media."""
    body = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(body) + len(chunk) > max_bytes:
            return None
        body.extend(chunk)
    return bytes(body) if body else None
