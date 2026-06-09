"""Provedor estático — dados embutidos (sorteio oficial + Elo calibrado).

É o default, funciona offline e sem rede. Não fornece resultados ao vivo:
o comportamento 'vivo' obtém-se por `update_real_score` manual ou compondo-o
com um `LocalFeedProvider`/provedor de API.
"""

from __future__ import annotations

from ...model.schemas import Match, Team
from ..ratings import build_group_stage_matches, build_teams
from .base import DataProvider


class StaticProvider(DataProvider):
    def load_teams(self) -> dict[str, Team]:
        return build_teams()

    def load_group_fixtures(self) -> list[Match]:
        return build_group_stage_matches()
