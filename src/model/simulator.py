"""Passo 2 — Esqueleto do simulador estatístico.

Calcula, para cada partida, as três variáveis de saída do sistema:
    1. predicted_score  -> placar mais provável
    2. expected_winner  -> seleção vencedora prevista (ou empate)
    3. confidence_level -> grau de confiança (%) do resultado previsto

Metodologia (alinhada com os repositórios de referência):
    Elo  ->  gols esperados (lambdas)  ->  Poisson bivariada com correção de
    Dixon-Coles  ->  grade de probabilidades de placar  ->  três variáveis.

A grade analítica é exata e determinística (ideal para a previsão por jogo);
`monte_carlo_match` amostra resultados e será o motor de propagação do
chaveamento (probabilidades de avanço) nas fases eliminatórias seguintes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import Match, MatchPrediction, Outcome, Score, Team

# ---------------------------------------------------------------------------
# Parâmetros do modelo (calibráveis). Valores iniciais razoáveis para Copa.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelParams:
    base_goals: float = 1.35       # λ base de uma partida equilibrada (Maher/DC)
    elo_divisor: float = 400.0     # 400 pts de Elo ~ +1 gol esperado de supremacia
    min_lambda: float = 0.3        # piso de λ (mantém variância realista)
    max_lambda: float = 3.5        # teto de λ
    dixon_coles_rho: float = -0.13  # ρ: corrige excesso de 0-0/1-1 (calibrado, ~-0.13)
    home_advantage: float = 75.0   # bónus de Elo só para anfitriãs (MEX/USA/CAN)
    max_goals: int = 8             # truncagem da grade de placares (0–8 por lado)


DEFAULT_PARAMS = ModelParams()


# ---------------------------------------------------------------------------
# 1) Elo -> gols esperados (lambdas)
# ---------------------------------------------------------------------------
def expected_goals(
    home: Team,
    away: Team,
    params: ModelParams = DEFAULT_PARAMS,
) -> tuple[float, float]:
    """Converte ratings Elo em gols esperados (λ_casa, λ_fora).

    Modelo linear de supremacia (Elo → λ), calibrado em ~920 internacionais:
        λ = clamp(base_goals + diferença_de_rating / 400, [min, max])

    A vantagem de casa só se aplica quando a equipa da casa é anfitriã do
    torneio (MEX/USA/CAN); o adversário sofre metade desse bónus, em sentido
    inverso — convenção do modelo de referência.
    """
    home_bonus = params.home_advantage if home.is_host else 0.0
    rating_home = home.elo + home.form_modifier
    rating_away = away.elo + away.form_modifier

    def _lam(attack: float, defense: float, bonus: float) -> float:
        lam = params.base_goals + (attack + bonus - defense) / params.elo_divisor
        return max(params.min_lambda, min(params.max_lambda, lam))

    lambda_home = _lam(rating_home, rating_away, home_bonus)
    lambda_away = _lam(rating_away, rating_home, -home_bonus / 2)
    return lambda_home, lambda_away


# ---------------------------------------------------------------------------
# 2) Poisson bivariada + correção de Dixon-Coles -> grade de placares
# ---------------------------------------------------------------------------
def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _dixon_coles_tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Fator τ que corrige a dependência nos placares baixos (0-0,1-0,0-1,1-1)."""
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(
    lambda_home: float,
    lambda_away: float,
    params: ModelParams = DEFAULT_PARAMS,
) -> list[list[float]]:
    """Matriz P[i][j] = P(casa marca i, fora marca j), normalizada."""
    n = params.max_goals + 1
    matrix = [[0.0] * n for _ in range(n)]
    total = 0.0
    for i in range(n):
        for j in range(n):
            p = (
                _dixon_coles_tau(i, j, lambda_home, lambda_away, params.dixon_coles_rho)
                * _poisson_pmf(i, lambda_home)
                * _poisson_pmf(j, lambda_away)
            )
            p = max(p, 0.0)  # τ pode ficar levemente negativo em casos extremos
            matrix[i][j] = p
            total += p
    if total > 0:
        for i in range(n):
            for j in range(n):
                matrix[i][j] /= total
    return matrix


# ---------------------------------------------------------------------------
# 3) Grade -> as três variáveis de saída
# ---------------------------------------------------------------------------
def _outcome_probabilities(matrix: list[list[float]]) -> tuple[float, float, float]:
    """Soma a grade em P(casa vence), P(empate), P(fora vence)."""
    p_home = p_draw = p_away = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
    return p_home, p_draw, p_away


def _top_scorelines(
    matrix: list[list[float]], k: int = 5
) -> list[tuple[tuple[int, int], float]]:
    flat = [
        ((i, j), p)
        for i, row in enumerate(matrix)
        for j, p in enumerate(row)
    ]
    flat.sort(key=lambda item: item[1], reverse=True)
    return flat[:k]


def _modal_scoreline_for_outcome(
    matrix: list[list[float]], outcome: Outcome
) -> tuple[int, int]:
    """Placar mais provável CONSISTENTE com o desfecho previsto.

    Evita a incoerência de prever 'vitória da casa' mas exibir um placar de
    empate: restringe a busca da moda às células compatíveis com `outcome`.
    """
    def matches(i: int, j: int) -> bool:
        if outcome is Outcome.HOME:
            return i > j
        if outcome is Outcome.AWAY:
            return i < j
        return i == j

    best_cell, best_p = (1, 1), -1.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if matches(i, j) and p > best_p:
                best_cell, best_p = (i, j), p
    return best_cell


def predict_match(
    home: Team,
    away: Team,
    params: ModelParams = DEFAULT_PARAMS,
    knockout: bool = False,
) -> MatchPrediction:
    """Produz a previsão completa de uma partida (placar, vencedor, confiança).

    Em fases eliminatórias (`knockout=True`) o empate não é um resultado final:
    a confiança e o vencedor são derivados condicionando a um desfecho decisivo
    (a massa de empate é redistribuída proporcionalmente entre casa e fora, o
    que aproxima a resolução por prorrogação/pênaltis de forma neutra).
    """
    lambda_home, lambda_away = expected_goals(home, away, params)
    matrix = score_matrix(lambda_home, lambda_away, params)
    p_home, p_draw, p_away = _outcome_probabilities(matrix)
    top = _top_scorelines(matrix)

    if knockout:
        # Sem empate como desfecho final: redistribui a massa de empate
        # proporcionalmente (aproxima resolução por prorrogação/pênaltis).
        decisive = p_home + p_away
        if decisive > 0:
            adj_home = p_home + p_draw * (p_home / decisive)
            adj_away = p_away + p_draw * (p_away / decisive)
        else:
            adj_home = adj_away = 0.5
        if adj_home >= adj_away:
            winner, confidence, best_outcome = home.team_id, adj_home, Outcome.HOME
        else:
            winner, confidence, best_outcome = away.team_id, adj_away, Outcome.AWAY
    else:
        probs = {Outcome.HOME: p_home, Outcome.DRAW: p_draw, Outcome.AWAY: p_away}
        best_outcome = max(probs, key=probs.__getitem__)
        confidence = probs[best_outcome]
        winner = {
            Outcome.HOME: home.team_id,
            Outcome.AWAY: away.team_id,
            Outcome.DRAW: None,
        }[best_outcome]

    # Placar previsto = moda da grade restrita ao desfecho previsto, garantindo
    # coerência entre placar e vencedor.
    best_i, best_j = _modal_scoreline_for_outcome(matrix, best_outcome)

    return MatchPrediction(
        predicted_score=Score(home_goals=best_i, away_goals=best_j),
        expected_winner=winner,
        confidence_level=round(confidence * 100, 1),
        prob_home=p_home,
        prob_draw=p_draw,
        prob_away=p_away,
        top_scorelines=top,
        expected_goals_home=round(lambda_home, 3),
        expected_goals_away=round(lambda_away, 3),
    )


# ---------------------------------------------------------------------------
# Motor de Monte Carlo (base para propagação do chaveamento — fase posterior)
# ---------------------------------------------------------------------------
def monte_carlo_match(
    home: Team,
    away: Team,
    n_sims: int = 10_000,
    params: ModelParams = DEFAULT_PARAMS,
    seed: int | None = None,
) -> dict:
    """Amostra `n_sims` placares e devolve frequências de desfecho/placares.

    Usa numpy quando disponível; caso contrário recorre a amostragem stdlib.
    Pensado para alimentar as probabilidades de AVANÇO no chaveamento (quando
    o torneio for simulado milhares de vezes nas fases eliminatórias).
    """
    lambda_home, lambda_away = expected_goals(home, away, params)
    try:
        import numpy as np

        rng = np.random.default_rng(seed)
        goals_home = rng.poisson(lambda_home, n_sims)
        goals_away = rng.poisson(lambda_away, n_sims)
        home_wins = int((goals_home > goals_away).sum())
        draws = int((goals_home == goals_away).sum())
        away_wins = n_sims - home_wins - draws
    except ModuleNotFoundError:  # pragma: no cover - fallback sem numpy
        import random

        rng = random.Random(seed)
        home_wins = draws = away_wins = 0
        for _ in range(n_sims):
            gh = _sample_poisson(rng, lambda_home)
            ga = _sample_poisson(rng, lambda_away)
            if gh > ga:
                home_wins += 1
            elif gh == ga:
                draws += 1
            else:
                away_wins += 1

    return {
        "prob_home": home_wins / n_sims,
        "prob_draw": draws / n_sims,
        "prob_away": away_wins / n_sims,
        "n_sims": n_sims,
    }


def _sample_poisson(rng, lam: float) -> int:  # pragma: no cover
    """Amostragem Poisson por algoritmo de Knuth (fallback sem numpy)."""
    target = math.exp(-lam)
    k, product = 0, 1.0
    while True:
        product *= rng.random()
        if product <= target:
            return k
        k += 1


# ---------------------------------------------------------------------------
# Orquestração: previsão da primeira rodada dos grupos (Passo 2)
# ---------------------------------------------------------------------------
def predict_first_round(
    matches: list[Match],
    teams: dict[str, Team],
    params: ModelParams = DEFAULT_PARAMS,
) -> list[Match]:
    """Preenche `prediction` de cada jogo da 1ª rodada e marca como PREDICTED."""
    for match in matches:
        home, away = teams[match.home_team], teams[match.away_team]
        match.prediction = predict_match(
            home, away, params, knockout=match.phase.is_knockout
        )
        match.status = match.status  # revalida via model_validator
        match._sync_status()
    return matches


# ---------------------------------------------------------------------------
# Demo executável: python -m src.model.simulator
# ---------------------------------------------------------------------------
def _demo() -> None:  # pragma: no cover - apresentação
    from ..data.ratings import build_first_round_matches, build_teams

    teams = build_teams()
    matches = predict_first_round(build_first_round_matches(), teams)

    print("\n=== PREVISÕES — 1ª RODADA DA FASE DE GRUPOS (sorteio oficial) ===\n")
    header = f"{'Jogo':<6}{'Partida':<40}{'Placar':<8}{'Vencedor':<14}{'Conf.':>6}"
    print(header)
    print("-" * len(header))
    has_provisional = False
    for m in matches:
        h, a = teams[m.home_team], teams[m.away_team]
        pred = m.prediction
        winner = teams[pred.expected_winner].name if pred.expected_winner else "Empate"
        provisional = h.is_placeholder or a.is_placeholder
        flag = " *" if provisional else ""
        has_provisional = has_provisional or provisional
        confronto = f"{h.name} x {a.name}{flag}"
        print(
            f"{m.match_id:<6}{confronto:<40}{str(pred.predicted_score):<8}"
            f"{winner:<14}{pred.confidence_level:>5.1f}%"
        )
    if has_provisional:
        print("\n* previsão PROVISÓRIA — envolve vaga de playoff ainda por definir.")
    print()


if __name__ == "__main__":  # pragma: no cover
    _demo()
