"""Provedor de API real (TEMPLATE de produção) — API-FOOTBALL (api-sports.io).

Demonstra como ligar uma fonte de dados ao vivo implementando a mesma interface
`DataProvider`. As seleções e o calendário continuam a vir do provedor base
(o sorteio/Elo que gerimos); este provedor acrescenta os RESULTADOS reais
buscados à API e mapeia-os para os nossos `match_id` ("A11", "m73", ...).

IMPORTANTE:
  • Requer a variável de ambiente API_FOOTBALL_KEY e acesso de rede ao host
    da API — bloqueado no ambiente sandbox, funcional em produção.
  • O mapeamento fixture-da-API -> match_id depende dos identificadores da
    competição na API; abaixo fica o esqueleto e o ponto exato a completar.
  • Usa apenas a biblioteca padrão (urllib), sem dependências extra.

Uso:
    provider = ApiFootballProvider(league=1, season=2026)
    engine = PredictionEngine(provider)
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional

from ...model.schemas import Match, Team
from .base import DataProvider
from .static import StaticProvider

_API_HOST = "https://v3.football.api-sports.io"


class ApiFootballProvider(DataProvider):
    def __init__(
        self,
        league: int,
        season: int,
        base: DataProvider | None = None,
        api_key: Optional[str] = None,
        fixture_map: Optional[dict[int, str]] = None,
    ):
        self.league = league
        self.season = season
        self.base = base or StaticProvider()
        self.api_key = api_key or os.environ.get("API_FOOTBALL_KEY")
        # Mapeia o id de fixture da API -> o nosso match_id ("A11", "m73", ...).
        self.fixture_map = fixture_map or {}

    # Seleções e calendário continuam a vir do provedor base.
    def load_teams(self) -> dict[str, Team]:
        return self.base.load_teams()

    def load_group_fixtures(self) -> list[Match]:
        return self.base.load_group_fixtures()

    # ------------------------------------------------------------------
    def _get(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "API_FOOTBALL_KEY não definida — configure a chave de API."
            )
        query = "&".join(f"{k}={v}" for k, v in params.items())
        req = urllib.request.Request(
            f"{_API_HOST}{path}?{query}",
            headers={"x-apisports-key": self.api_key},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec - host fixo
            return json.loads(resp.read().decode("utf-8"))

    def fetch_results(self) -> dict[str, tuple[int, int]]:
        """Busca os jogos terminados na API e devolve match_id -> (casa, fora)."""
        payload = self._get(
            "/fixtures",
            {"league": self.league, "season": self.season, "status": "FT"},
        )
        results: dict[str, tuple[int, int]] = {}
        for item in payload.get("response", []):
            fixture_id = item["fixture"]["id"]
            match_id = self.fixture_map.get(fixture_id)
            if match_id is None:
                continue  # TODO: completar o mapeamento fixture-da-API -> match_id
            goals = item.get("goals", {})
            home, away = goals.get("home"), goals.get("away")
            if home is not None and away is not None:
                results[match_id] = (int(home), int(away))
        return results
