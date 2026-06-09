"""Ratings das seleções e fixtures da fase de grupos — DADOS REAIS.

Fontes:
  • Composição dos 12 grupos (A–L): sorteio oficial da Copa 2026.
  • Ratings Elo: calibração sobre ~920 jogos internacionais reais
    (Out/2023–Mai/2026), método Elo + recência + importância da competição
    (adaptado da lógica de world-cup-2026-prediction-model).

Notas:
  • 6 vagas continuam por definir (vencedores dos playoffs UEFA A–D e
    Intercontinentais 1–2). Entram como placeholders (`is_placeholder=True`)
    com Elo provisório e DEVEM ser resolvidas quando os playoffs terminarem
    — o sistema 'vivo' substitui o team_id e o Elo nessa altura.
  • Anfitriãs (MEX/USA/CAN) recebem vantagem de casa nos seus jogos.
"""

from __future__ import annotations

from ..model.schemas import Match, Phase, Team

# Elo provisório para vagas de playoff ainda não resolvidas.
_UEFA_PLAYOFF_ELO = 1700.0   # vencedor típico de playoff europeu
_IC_PLAYOFF_ELO = 1500.0     # vencedor típico de playoff intercontinental

_HOSTS = {"MEX", "USA", "CAN"}

# (code, nome, elo, is_placeholder) na ordem oficial de cada grupo (pote/seed).
_RAW_GROUPS: dict[str, list[tuple[str, str, float, bool]]] = {
    "A": [("MEX", "México", 1830, False), ("RSA", "África do Sul", 1562, False),
          ("KOR", "Coreia do Sul", 1745, False), ("UEFA-D", "Vencedor Playoff UEFA D", _UEFA_PLAYOFF_ELO, True)],
    "B": [("CAN", "Canadá", 1725, False), ("SUI", "Suíça", 1811, False),
          ("QAT", "Catar", 1554, False), ("UEFA-A", "Vencedor Playoff UEFA A", _UEFA_PLAYOFF_ELO, True)],
    "C": [("BRA", "Brasil", 1994, False), ("MAR", "Marrocos", 1875, False),
          ("HAI", "Haiti", 1481, False), ("SCO", "Escócia", 1618, False)],
    "D": [("USA", "Estados Unidos", 1794, False), ("PAR", "Paraguai", 1653, False),
          ("AUS", "Austrália", 1769, False), ("UEFA-C", "Vencedor Playoff UEFA C", _UEFA_PLAYOFF_ELO, True)],
    "E": [("GER", "Alemanha", 1928, False), ("ECU", "Equador", 1790, False),
          ("CIV", "Costa do Marfim", 1706, False), ("CUW", "Curaçao", 1543, False)],
    "F": [("NED", "Países Baixos", 1946, False), ("JPN", "Japão", 1851, False),
          ("TUN", "Tunísia", 1666, False), ("UEFA-B", "Vencedor Playoff UEFA B", _UEFA_PLAYOFF_ELO, True)],
    "G": [("BEL", "Bélgica", 1872, False), ("IRN", "Irã", 1735, False),
          ("EGY", "Egito", 1672, False), ("NZL", "Nova Zelândia", 1569, False)],
    "H": [("ESP", "Espanha", 2075, False), ("URU", "Uruguai", 1833, False),
          ("KSA", "Arábia Saudita", 1620, False), ("CPV", "Cabo Verde", 1551, False)],
    "I": [("FRA", "França", 2042, False), ("SEN", "Senegal", 1830, False),
          ("NOR", "Noruega", 1814, False), ("IC-2", "Vencedor Playoff Interc. 2", _IC_PLAYOFF_ELO, True)],
    "J": [("ARG", "Argentina", 2064, False), ("AUT", "Áustria", 1795, False),
          ("ALG", "Argélia", 1676, False), ("JOR", "Jordânia", 1515, False)],
    "K": [("POR", "Portugal", 1935, False), ("COL", "Colômbia", 1884, False),
          ("UZB", "Uzbequistão", 1638, False), ("IC-1", "Vencedor Playoff Interc. 1", _IC_PLAYOFF_ELO, True)],
    "L": [("ENG", "Inglaterra", 1982, False), ("CRO", "Croácia", 1878, False),
          ("GHA", "Gana", 1635, False), ("PAN", "Panamá", 1582, False)],
}


def build_teams() -> dict[str, Team]:
    """Constrói o dicionário team_id -> Team a partir do sorteio oficial."""
    teams: dict[str, Team] = {}
    for group, rows in _RAW_GROUPS.items():
        for code, name, elo, placeholder in rows:
            teams[code] = Team(
                team_id=code,
                name=name,
                group=group,
                elo=elo,
                is_host=code in _HOSTS,
                is_placeholder=placeholder,
            )
    return teams


# Calendário round-robin de um grupo de 4 (índices), por rodada (matchday).
_ROUND_ROBIN: dict[int, list[tuple[int, int]]] = {
    1: [(0, 1), (2, 3)],
    2: [(0, 2), (3, 1)],
    3: [(0, 3), (1, 2)],
}


def build_first_round_matches() -> list[Match]:
    """Gera os jogos da PRIMEIRA RODADA de cada grupo (matchday 1).

    Na 1ª rodada jogam-se seed1 x seed2 e seed3 x seed4. Resultam 24 partidas
    (12 grupos x 2 jogos). Seis envolvem uma vaga de playoff (TBD) e ficam com
    previsão PROVISÓRIA até resolução.
    """
    return [m for m in build_group_stage_matches() if m.matchday == 1]


def build_group_stage_matches() -> list[Match]:
    """Gera os 72 jogos da fase de grupos (12 grupos x round-robin de 6).

    O conjunto de confrontos é o que determina a classificação; a ordem exacta
    do calendário oficial pode ser ligada depois sem alterar as tabelas finais.
    """
    matches: list[Match] = []
    for group, rows in _RAW_GROUPS.items():
        ids = [r[0] for r in rows]
        for matchday, pairings in _ROUND_ROBIN.items():
            for slot, (h, a) in enumerate(pairings, start=1):
                matches.append(
                    Match(
                        match_id=f"{group}{matchday}{slot}",
                        phase=Phase.GROUP_STAGE,
                        home_team=ids[h],
                        away_team=ids[a],
                        group=group,
                        matchday=matchday,
                    )
                )
    return matches
