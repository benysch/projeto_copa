"""Provedor de feed local — simula dados ao vivo a partir de um ficheiro JSON.

Compõe-se sobre um provedor base (por omissão `StaticProvider`): herda dele as
seleções e o calendário, e acrescenta os resultados reais e as resoluções de
playoff lidos de um ficheiro. Serve para:
  • desenvolver/testar o fluxo 'vivo' sem depender de rede,
  • servir de modelo a um provedor de API real (basta substituir a leitura do
    ficheiro por chamadas HTTP à API e mapear a resposta para o mesmo formato).

Formato do ficheiro (todos os campos opcionais):
    {
      "results":  { "A11": [2, 0], "A12": [1, 1] },
      "playoffs": { "UEFA-A": {"name": "Itália", "elo": 1860} }
    }
"""

from __future__ import annotations

import json
from pathlib import Path

from ...model.schemas import Match, Team
from .base import DataProvider
from .static import StaticProvider


class LocalFeedProvider(DataProvider):
    def __init__(self, feed_path: str | Path, base: DataProvider | None = None):
        self.feed_path = Path(feed_path)
        self.base = base or StaticProvider()

    def _read_feed(self) -> dict:
        if not self.feed_path.exists():
            return {}
        with self.feed_path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def load_teams(self) -> dict[str, Team]:
        return self.base.load_teams()

    def load_group_fixtures(self) -> list[Match]:
        return self.base.load_group_fixtures()

    def fetch_results(self) -> dict[str, tuple[int, int]]:
        feed = self._read_feed()
        return {mid: (int(h), int(a)) for mid, (h, a) in feed.get("results", {}).items()}

    def resolve_placeholders(self) -> dict[str, Team]:
        feed = self._read_feed()
        teams = self.base.load_teams()
        resolved: dict[str, Team] = {}
        for slot_id, info in feed.get("playoffs", {}).items():
            base_team = teams.get(slot_id)
            group = base_team.group if base_team else None
            resolved[slot_id] = Team(
                team_id=slot_id,  # mantém o id do slot para preservar os fixtures
                name=info["name"],
                group=group,
                elo=float(info["elo"]),
                is_placeholder=False,
            )
        return resolved
