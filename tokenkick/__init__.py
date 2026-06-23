"""TokenKick package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tokenkick")
except PackageNotFoundError:
    __version__ = "unknown"
