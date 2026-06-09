"""Interface de provedor de dados.

Um `DataProvider` é a única fronteira entre o motor de previsão e a ORIGEM dos
dados. Trocar dados estáticos por uma API ao vivo (API-FOOTBALL, football-data.org)
significa apenas implementar esta interface — o motor e o MCP não mudam.

Contrato:
    load_teams()           -> seleções com ratings (snapshot inicial)
    load_group_fixtures()  -> os 72 jogos da fase de grupos
    fetch_results()        -> resultados REAIS já conhecidos (vivo)
    resolve_placeholders() -> vagas de playoff entretanto decididas
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ...model.schemas import Match, Team


class DataProvider(ABC):
    """Fonte de dados para o motor de previsão."""

    @abstractmethod
    def load_teams(self) -> dict[str, Team]:
        """Dicionário team_id -> Team com ratings Elo."""

    @abstractmethod
    def load_group_fixtures(self) -> list[Match]:
        """Lista dos jogos da fase de grupos (sem resultado preenchido)."""

    def fetch_results(self) -> dict[str, tuple[int, int]]:
        """Resultados reais conhecidos: match_id -> (gols_casa, gols_fora).

        Default vazio (sem dados ao vivo). Provedores ligados a uma API/feed
        devolvem aqui os placares já disputados.
        """
        return {}

    def resolve_placeholders(self) -> dict[str, Team]:
        """Vagas de playoff resolvidas: placeholder_id -> Team já definida.

        Default vazio. Permite ao sistema 'vivo' substituir 'UEFA-A' etc. pela
        seleção real assim que o playoff for disputado.
        """
        return {}
