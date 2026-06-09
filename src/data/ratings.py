"""Ratings das seleções e fixtures da fase de grupos.

ATENÇÃO: os valores Elo e a composição dos 12 grupos abaixo são ILUSTRATIVOS,
servem para o simulador correr de ponta a ponta enquanto o sistema está em
construção. Devem ser substituídos pelos dados oficiais do sorteio da Copa 2026
e por ratings Elo calibrados (ver `calibrate` na lógica de referência) assim que
a fonte de dados real estiver ligada ao pipeline.

Elo aproximado baseado em ordens de grandeza públicas (eloratings.net) ~mai/2026.
"""

from __future__ import annotations

from ..model.schemas import Match, Phase, Team

# ---------------------------------------------------------------------------
# Catálogo de seleções (team_id -> Team), organizado pelos 12 grupos A–L.
# ---------------------------------------------------------------------------
_RAW_GROUPS: dict[str, list[tuple[str, str, float, int]]] = {
    # grupo: [(team_id, nome, elo, fifa_rank), ...]
    "A": [("MEX", "México", 1885, 14), ("CAN", "Canadá", 1875, 30),
          ("CRO", "Croácia", 1960, 10), ("KSA", "Arábia Saudita", 1660, 58)],
    "B": [("USA", "Estados Unidos", 1830, 16), ("WAL", "País de Gales", 1800, 28),
          ("SEN", "Senegal", 1900, 18), ("QAT", "Catar", 1640, 53)],
    "C": [("ARG", "Argentina", 2120, 1), ("POL", "Polônia", 1820, 27),
          ("JPN", "Japão", 1900, 17), ("RSA", "África do Sul", 1700, 60)],
    "D": [("FRA", "França", 2080, 2), ("DEN", "Dinamarca", 1900, 21),
          ("MAR", "Marrocos", 1920, 12), ("PAN", "Panamá", 1700, 41)],
    "E": [("ESP", "Espanha", 2070, 3), ("ECU", "Equador", 1820, 23),
          ("KOR", "Coreia do Sul", 1820, 22), ("GHA", "Gana", 1720, 70)],
    "F": [("BRA", "Brasil", 2040, 5), ("SUI", "Suíça", 1850, 19),
          ("NGA", "Nigéria", 1810, 39), ("UZB", "Uzbequistão", 1640, 57)],
    "G": [("ENG", "Inglaterra", 2010, 4), ("SRB", "Sérvia", 1840, 31),
          ("EGY", "Egito", 1790, 36), ("NZL", "Nova Zelândia", 1500, 86)],
    "H": [("POR", "Portugal", 2030, 6), ("URU", "Uruguai", 1930, 11),
          ("AUS", "Austrália", 1750, 24), ("CIV", "Costa do Marfim", 1780, 40)],
    "I": [("NED", "Países Baixos", 1990, 7), ("AUT", "Áustria", 1850, 25),
          ("TUN", "Tunísia", 1720, 50), ("JOR", "Jordânia", 1620, 64)],
    "J": [("BEL", "Bélgica", 1980, 8), ("COL", "Colômbia", 1920, 13),
          ("CMR", "Camarões", 1730, 52), ("CRC", "Costa Rica", 1700, 54)],
    "K": [("ITA", "Itália", 1960, 9), ("ALG", "Argélia", 1760, 38),
          ("PER", "Peru", 1740, 49), ("PAR", "Paraguai", 1760, 47)],
    "L": [("GER", "Alemanha", 1970, 15), ("CHI", "Chile", 1760, 45),
          ("IRN", "Irã", 1780, 20), ("HAI", "Haiti", 1480, 90)],
}


def build_teams() -> dict[str, Team]:
    """Constrói o dicionário team_id -> Team a partir do catálogo bruto."""
    teams: dict[str, Team] = {}
    for group, rows in _RAW_GROUPS.items():
        for team_id, name, elo, rank in rows:
            teams[team_id] = Team(
                team_id=team_id, name=name, group=group, elo=elo, fifa_rank=rank
            )
    return teams


def build_first_round_matches() -> list[Match]:
    """Gera os jogos da PRIMEIRA RODADA de cada grupo (matchday 1).

    Convenção FIFA: na 1ª rodada jogam-se equipa1 x equipa2 e equipa3 x equipa4.
    Resultam 24 partidas (12 grupos x 2 jogos).
    """
    matches: list[Match] = []
    for group, rows in _RAW_GROUPS.items():
        ids = [r[0] for r in rows]
        pairings = [(ids[0], ids[1]), (ids[2], ids[3])]
        for idx, (home, away) in enumerate(pairings, start=1):
            matches.append(
                Match(
                    match_id=f"{group}{idx}",
                    phase=Phase.GROUP_STAGE,
                    home_team=home,
                    away_team=away,
                    group=group,
                    matchday=1,
                )
            )
    return matches
