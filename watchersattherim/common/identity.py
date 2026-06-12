"""LXMF identity helpers, shared by all roles.

A node's LXMF address is the hash of its ``lxmf.delivery`` destination - these
helpers persist the RNS identity and compute that address. RNS is imported
lazily so the helpers can be referenced without a running Reticulum instance.
"""

from __future__ import annotations

import os


def load_or_create_identity(path: str):
    """Load the persisted RNS identity, creating and saving one if absent."""
    import RNS

    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.isfile(path):
        return RNS.Identity.from_file(path)
    identity = RNS.Identity()
    identity.to_file(path)
    return identity


def lxmf_address(identity) -> str:
    """The node's LXMF address (delivery destination hash) as hex."""
    import RNS

    return RNS.Destination.hash(identity, "lxmf", "delivery").hex()


def print_identity(identity_path: str) -> str:
    """Print (and return) the LXMF address, creating the identity if needed."""
    address = lxmf_address(load_or_create_identity(identity_path))
    print(address)
    return address
