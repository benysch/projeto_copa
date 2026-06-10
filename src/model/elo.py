"""Recalibração contínua de Elo durante o torneio.

Atualiza os ratings à medida que entram resultados REAIS, com a fórmula
clássica do World Football Elo Ratings (eloratings.net):

    delta = K * G * (W - We)

    W  = resultado real (1 vitória, 0.5 empate, 0 derrota), do lado da casa
    We = resultado esperado = 1 / (1 + 10^(-(elo_casa + bónus - elo_fora)/400))
    G  = multiplicador de margem de gols (goleadas valem mais)
    K  = peso da competição (60 na Copa do Mundo; usamos 50 por omissão,
         ligeiramente conservador para não sobre-reagir a um jogo isolado)

A recalibração é IDEMPOTENTE no motor: a cada `refresh()` os ratings voltam ao
snapshot base e os deltas são reaplicados em ordem cronológica, pelo que
corrigir/remover um resultado nunca deixa resíduo no rating.
"""

from __future__ import annotations

from .schemas import Team

# K-factor por omissão para jogos de Copa do Mundo.
WORLD_CUP_K = 50.0


def expected_result(elo_home: float, elo_away: float, home_bonus: float = 0.0) -> float:
    """Probabilidade Elo de vitória da casa (empate conta como meio ponto)."""
    return 1.0 / (1.0 + 10.0 ** (-((elo_home + home_bonus) - elo_away) / 400.0))


def goal_multiplier(goal_diff: int) -> float:
    """Multiplicador G da margem de gols (convenção eloratings.net)."""
    diff = abs(goal_diff)
    if diff <= 1:
        return 1.0
    if diff == 2:
        return 1.5
    return (11.0 + diff) / 8.0


def rating_delta(
    elo_home: float,
    elo_away: float,
    home_goals: int,
    away_goals: int,
    k: float = WORLD_CUP_K,
    home_bonus: float = 0.0,
) -> float:
    """Delta de Elo do lado da CASA (a fora recebe o simétrico)."""
    if home_goals > away_goals:
        w = 1.0
    elif home_goals < away_goals:
        w = 0.0
    else:
        w = 0.5
    we = expected_result(elo_home, elo_away, home_bonus)
    return k * goal_multiplier(home_goals - away_goals) * (w - we)


def apply_result(
    home: Team,
    away: Team,
    home_goals: int,
    away_goals: int,
    k: float = WORLD_CUP_K,
    home_bonus: float = 0.0,
) -> float:
    """Aplica o resultado aos ratings das duas seleções; devolve o delta da casa."""
    delta = rating_delta(home.elo, away.elo, home_goals, away_goals, k, home_bonus)
    home.elo += delta
    away.elo -= delta
    return delta
