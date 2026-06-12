"""The monitor's LXMF sender (telemetry -> collector).

Untested in CI (needs a running Reticulum instance). The batch travels in an LXMF
custom field; LXMF handles the msgpack wire encoding. Transport mechanics live in
``common.lxmf``.
"""

from __future__ import annotations

from typing import Callable

from ..common import lxmf
from ..common.protocol import APP_OBS
from .config import Config


class LiveLxmfSender:
    """Sends telemetry batches to the collector over LXMF.

    The collector destination is resolved lazily, so the monitor can start before
    a path to the collector is known. Until it resolves, ``send`` returns False
    and the telemetry re-queues (path discovery is kicked off meanwhile).
    """

    def __init__(self, router, source, dest_hash: bytes, desired_method=None,
                 app_type: bytes = APP_OBS):
        self.router = router
        self.source = source
        self.dest_hash = dest_hash
        self.desired_method = desired_method
        self.app_type = app_type

    def send(self, batch: dict) -> bool:
        try:
            import LXMF

            dest = lxmf.resolve(self.dest_hash)
            if dest is None:
                return False
            fields = {LXMF.FIELD_CUSTOM_TYPE: self.app_type, LXMF.FIELD_CUSTOM_DATA: batch}
            lxmf.send(self.router, self.source, dest,
                      fields=fields, method=self.desired_method)
            return True
        except Exception:
            return False


def make_lxmf_sender(config: Config, log: Callable[[str], None] = lambda _m: None) -> LiveLxmfSender:
    import LXMF
    import RNS

    router, source = lxmf.setup(
        identity_path=config.storage.identity_path,
        storage_dir=config.storage.dir,
        display_name="watchersattherim monitor",
        reticulum_config_dir=config.reticulum.config_dir,
    )
    log(f"monitor LXMF address: {RNS.prettyhexrep(source.hash)}")

    if config.collector.delivery == "propagated":
        method = LXMF.LXMessage.PROPAGATED
        if config.collector.propagation_node:
            router.set_outbound_propagation_node(bytes.fromhex(config.collector.propagation_node))
    else:
        method = LXMF.LXMessage.DIRECT

    return LiveLxmfSender(
        router, source, bytes.fromhex(config.collector.address), desired_method=method
    )
