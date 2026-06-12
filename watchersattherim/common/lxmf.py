"""Reticulum/LXMF transport helpers shared by the monitor, collector, and query.

The three recurring LXMF operations, in one place: bootstrap a router + our own
delivery destination, resolve a peer's delivery destination from its hash, and
send a message. RNS/LXMF are imported lazily.

Untested in CI (needs a running Reticulum instance) - validate on a deployment
host. The receive/correlate logic (callbacks, request-id matching) stays in each
role, since it differs per role.
"""

from __future__ import annotations

import os
from typing import Optional


def setup(*, identity_path: str, storage_dir: str, display_name: str,
          reticulum_config_dir: Optional[str] = None):
    """Initialise Reticulum + an LXMF router. Returns (router, source_destination)."""
    import LXMF
    import RNS

    from .identity import load_or_create_identity

    cfgdir = os.path.expanduser(reticulum_config_dir) if reticulum_config_dir else None
    RNS.Reticulum(configdir=cfgdir)

    storage = os.path.expanduser(storage_dir)
    os.makedirs(storage, exist_ok=True)
    identity = load_or_create_identity(identity_path)

    lxmf_dir = os.path.join(storage, "lxmf")
    os.makedirs(lxmf_dir, exist_ok=True)
    router = LXMF.LXMRouter(identity=identity, storagepath=lxmf_dir)
    source = router.register_delivery_identity(identity, display_name=display_name)
    return router, source


def resolve(dest_hash: bytes):
    """Resolve a peer's LXMF delivery destination, or None if no path yet.

    When the identity isn't known, kicks off path discovery so a later call can
    succeed.
    """
    import RNS

    identity = RNS.Identity.recall(dest_hash)
    if identity is None:
        if not RNS.Transport.has_path(dest_hash):
            RNS.Transport.request_path(dest_hash)
        return None
    return RNS.Destination(
        identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery"
    )


def send(router, source, dest, *, fields=None, content=b"", method=None) -> None:
    """Build and hand an LXMF message to the router for outbound delivery."""
    import LXMF

    lxm = LXMF.LXMessage(
        dest, source, content, fields=fields,
        desired_method=method or LXMF.LXMessage.DIRECT,
    )
    router.handle_outbound(lxm)
