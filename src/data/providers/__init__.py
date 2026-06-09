"""Provedores de dados — abstraem a origem dos dados (estática, feed, API)."""

from .api_football import ApiFootballProvider
from .base import DataProvider
from .livescore import LiveScoreMcpProvider
from .local_feed import LocalFeedProvider
from .static import StaticProvider

__all__ = [
    "DataProvider",
    "StaticProvider",
    "LocalFeedProvider",
    "ApiFootballProvider",
    "LiveScoreMcpProvider",
]
