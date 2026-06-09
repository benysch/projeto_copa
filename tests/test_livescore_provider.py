"""Testa o LiveScoreMcpProvider contra um servidor MCP mock in-memory.

Prova o pipeline de ingestão (cliente MCP -> normalização -> mapeamento para os
nossos match_id) sem depender do host real (bloqueado na sandbox).
"""

from __future__ import annotations

from fastmcp import Client, FastMCP

from src.data.providers import LiveScoreMcpProvider
from src.service.engine import PredictionEngine

# Servidor MCP falso a imitar o livescore-mcp.
mock = FastMCP(name="mock-livescore")


@mock.tool
def get_fixtures(competition: str) -> dict:
    """Devolve jogos da Copa no formato (aproximado) de um feed de placares."""
    return {
        "matches": [
            {"home_team": "Mexico", "away_team": "South Africa",
             "home_score": 3, "away_score": 1, "status": "FT"},
            {"home_team": "Brazil", "away_team": "Morocco",
             "home_score": 2, "away_score": 0, "status": "finished"},
            # Orientação invertida vs. a nossa (nós: Inglaterra em casa).
            {"home_team": "Croatia", "away_team": "England",
             "home_score": 1, "away_score": 0, "status": "FT"},
            # Jogo ao vivo (não terminado) -> deve ser ignorado.
            {"home_team": "Spain", "away_team": "Uruguay",
             "home_score": 1, "away_score": 1, "status": "live"},
        ]
    }


def _provider() -> LiveScoreMcpProvider:
    return LiveScoreMcpProvider(client_factory=lambda: Client(mock))


def test_fetch_results_maps_to_match_ids():
    results = _provider().fetch_results()
    # Mexico 3-1 South Africa -> grupo A, jogo A11 (MEX em casa).
    assert results["A11"] == (3, 1)
    # Brazil 2-0 Morocco -> grupo C, jogo C11 (BRA em casa).
    assert results["C11"] == (2, 0)


def test_orientation_is_corrected():
    results = _provider().fetch_results()
    # Feed: Croatia 1-0 England. Nós temos Inglaterra em casa (L11), logo
    # o placar é reorientado para (Inglaterra 0, Croácia 1).
    assert results["L11"] == (0, 1)


def test_unfinished_match_ignored():
    results = _provider().fetch_results()
    # Spain x Uruguay estava 'live' -> não entra.
    assert not any(v for k, v in results.items() if k.startswith("H1"))


def test_name_resolution_aliases():
    p = _provider()
    assert p._resolve_team("United States") == "USA"
    assert p._resolve_team("Korea Republic") == "KOR"
    assert p._resolve_team("Côte d'Ivoire") == "CIV"
    assert p._resolve_team("Desconhecidos FC") is None


def test_engine_ingests_from_livescore():
    """O motor usa o provedor de placares e reflete os resultados na tabela."""
    eng = PredictionEngine(_provider())
    a11 = eng._find_match("A11")
    assert a11.is_finished and a11.real_score.home_goals == 3
    # México (3-1) deve liderar o grupo A.
    assert eng.standings.position("A", 1).team_id == "MEX"
