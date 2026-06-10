"""Testa o LiveScoreMcpProvider contra um servidor MCP mock in-memory.

O mock imita o PROTOCOLO REAL do livescore-mcp (validado ao vivo em 2026-06-10):
`get_day_fixtures(date='DD/MM/YYYY')` devolve TEXTO com um cabeçalho seguido de
JSON aninhado país -> ligas -> jogos, com `localteam`/`visitorteam`/`scoretime`.
"""

from __future__ import annotations

import json
from datetime import date

from fastmcp import Client, FastMCP

from src.data.providers import LiveScoreMcpProvider
from src.service.engine import PredictionEngine

# Servidor MCP falso a imitar o livescore-mcp real.
mock = FastMCP(name="mock-livescore")

_DAY_ONE = [
    {
        "country": "World Cup",
        "leagues": [
            {
                "league": "FIFA World Cup",
                "matches": [
                    {"localteam": "Mexico", "visitorteam": "South Africa",
                     "scoretime": "3 - 1", "status": "FT"},
                    {"localteam": "Brazil", "visitorteam": "Morocco",
                     "scoretime": "2 - 0", "status": "AET"},
                    # Orientação invertida vs. a nossa (nós: Inglaterra em casa).
                    {"localteam": "Croatia", "visitorteam": "England",
                     "scoretime": "1 - 0", "status": "FT"},
                    # Jogo ao vivo (não terminado) -> deve ser ignorado.
                    {"localteam": "Spain", "visitorteam": "Uruguay",
                     "scoretime": "1 - 1", "status": "45'"},
                    # Ainda não começou -> sem placar.
                    {"localteam": "Canada", "visitorteam": "Switzerland",
                     "scoretime": " - ", "status": "Not Started"},
                ],
            }
        ],
    },
    {
        "country": "Brazil",
        "leagues": [
            {
                "league": "Serie A",
                "matches": [
                    # Liga doméstica: NÃO pode entrar nos resultados da Copa.
                    {"localteam": "Flamengo", "visitorteam": "Palmeiras",
                     "scoretime": "4 - 0", "status": "FT"},
                ],
            }
        ],
    },
]

CALLS: list[str] = []


@mock.tool
def get_day_fixtures(date: str) -> str:
    """Imita a resposta real: texto com cabeçalho + JSON aninhado."""
    CALLS.append(date)
    payload = _DAY_ONE if date == "11/06/2026" else []
    return f"Fixtures for {date}:\n\n{json.dumps(payload)}"


def _provider() -> LiveScoreMcpProvider:
    p = LiveScoreMcpProvider(client_factory=lambda: Client(mock))
    p._today = lambda: date(2026, 6, 12)  # congela 'hoje' para o teste
    return p


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


def test_unfinished_and_unstarted_matches_ignored():
    results = _provider().fetch_results()
    assert not any(k.startswith("H1") for k in results)  # Spain x Uruguay ao vivo
    assert not any(k.startswith("B1") for k in results)  # Canada x Suíça não começou


def test_domestic_league_filtered_out():
    results = _provider().fetch_results()
    assert len(results) == 3  # só os 3 jogos terminados da Copa


def test_closed_days_are_cached():
    p = _provider()
    CALLS.clear()
    p.fetch_results()
    first = list(CALLS)
    p.fetch_results()
    # 11/06 (dia encerrado) vem do cache; só 12/06 ('hoje') é rebuscado.
    assert first.count("11/06/2026") == 1
    assert CALLS.count("11/06/2026") == 1
    assert CALLS.count("12/06/2026") == 2


def test_before_tournament_returns_empty():
    p = LiveScoreMcpProvider(client_factory=lambda: Client(mock))
    p._today = lambda: date(2026, 6, 1)
    assert p.fetch_results() == {}


def test_name_resolution_aliases():
    p = _provider()
    assert p._resolve_team("United States") == "USA"
    assert p._resolve_team("Korea Republic") == "KOR"
    assert p._resolve_team("Côte d'Ivoire") == "CIV"
    assert p._resolve_team("Türkiye") == "TUR"
    assert p._resolve_team("Czech Republic") == "CZE"
    assert p._resolve_team("DR Congo") == "COD"
    assert p._resolve_team("Desconhecidos FC") is None


def test_engine_ingests_from_livescore():
    """O motor usa o provedor de placares e reflete os resultados na tabela."""
    eng = PredictionEngine(_provider())
    a11 = eng._find_match("A11")
    assert a11.is_finished and a11.real_score.home_goals == 3
    # México (3-1) deve liderar o grupo A.
    assert eng.standings.position("A", 1).team_id == "MEX"
