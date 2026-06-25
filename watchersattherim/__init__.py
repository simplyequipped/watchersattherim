"""watchersattherim - FT8 propagation observatory (monitor + collector)."""

from importlib.metadata import PackageNotFoundError, version

from .common.config import ConfigError

try:
    __version__ = version("watchersattherim")
except PackageNotFoundError:
    # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

__all__ = ["ConfigError", "__version__"]
