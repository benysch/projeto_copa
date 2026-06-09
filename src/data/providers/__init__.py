"""Provedores de dados — abstraem a origem dos dados (estática, feed, API)."""

from .base import DataProvider
from .local_feed import LocalFeedProvider
from .static import StaticProvider

__all__ = ["DataProvider", "StaticProvider", "LocalFeedProvider"]
