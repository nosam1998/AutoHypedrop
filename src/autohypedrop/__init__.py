"""AutoHypedrop: open the free drops on your own hypedrop.com account."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("autohypedrop")
except PackageNotFoundError:  # running from a source checkout that isn't installed
    __version__ = "0.0.0+unknown"
