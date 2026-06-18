"""Provedores de dados — abstraem a origem dos dados (estática, feed, API)."""

import os
from pathlib import Path

from .api_football import ApiFootballProvider
from .base import DataProvider
from .livescore import LiveScoreMcpProvider
from .local_feed import LocalFeedProvider
from .static import StaticProvider

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def build_provider() -> DataProvider:
    """Escolhe a fonte de dados pela variável de ambiente WC2026_PROVIDER.

    Partilhado pelo servidor MCP e pelo briefing diário (daily_briefing), para
    que ambos vejam exatamente os mesmos dados.
    """
    kind = os.environ.get("WC2026_PROVIDER", "static").strip().lower()
    if kind == "feed":
        default_feed = _REPO_ROOT / "data" / "sample_feed.json"
        return LocalFeedProvider(os.environ.get("WC2026_FEED_PATH", str(default_feed)))
    if kind == "livescore":
        url = os.environ.get("WC2026_LIVESCORE_URL", "https://livescoremcp.com/sse")
        return LiveScoreMcpProvider(server_url=url)
    if kind == "api":
        return ApiFootballProvider(
            league=int(os.environ.get("WC2026_API_LEAGUE", "1")),
            season=int(os.environ.get("WC2026_API_SEASON", "2026")),
        )
    return StaticProvider()


__all__ = [
    "DataProvider",
    "StaticProvider",
    "LocalFeedProvider",
    "ApiFootballProvider",
    "LiveScoreMcpProvider",
    "build_provider",
]
