"""Wire codec for the LXMF custom-field payloads.

A payload object is serialized with the umsgpack that ships with RNS and then
gzipped. Every monitor->collector and collector<->client message rides through
here, so the telemetry batch, the query envelope, and the reply all travel
identically and the format lives in one place. The compressed bytes go in LXMF's
FIELD_CUSTOM_DATA under the message's FIELD_CUSTOM_TYPE.
"""

from __future__ import annotations

import gzip

from RNS.vendor import umsgpack


def encode(payload) -> bytes:
    """Serialize then compress a payload. mtime=0 keeps output stable."""
    return gzip.compress(umsgpack.packb(payload), mtime=0)


def decode(data: bytes):
    """Inverse of encode: decompress then deserialize."""
    return umsgpack.unpackb(gzip.decompress(data))
