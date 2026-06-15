"""Propagation inference layer.

Turns the collector's raw observations into channel/trend/map/coverage queries.
Pure functions over a database connection (stdlib only); mounted into the
collector's query dispatch and served over HTTP and LXMF. A standalone
``watr-propagation`` CLI is kept for local one-shot use. See docs/PROPAGATION.md.
"""

from . import channel, field, geo, metrics, trend
from .config import PropagationConfig

__all__ = ["channel", "trend", "field", "geo", "metrics", "PropagationConfig"]
