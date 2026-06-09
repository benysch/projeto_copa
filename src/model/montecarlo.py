"""Passo 4 — Simulação de Monte Carlo do torneio completo.

Enquanto o modo determinístico (`tournament.py`) dá o cenário MODAL (favorito
vence sempre), o Monte Carlo amostra milhares de torneios inteiros para estimar
as PROBABILIDADES reais de cada seleção:
    • avançar da fase de grupos (top 2 ou melhor terceiro)
    • alcançar oitavas / quartas / semis / final
    • ser campeã

Metodologia: amostra o placar de cada jogo a partir do λ de Poisson (Elo →
gols); nas eliminatórias, empates resolvem-se por "pênaltis" com probabilidade
logística sobre a diferença de Elo (regra do modelo de referência).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from ..data.ratings import build_group_stage_matches, build_teams
from .bracket import (
    FINAL,
    QUARTER_FINALS,
    ROUND_OF_16,
    ROUND_OF_32,
    SEMI_FINALS,
    THIRD_PLACE,
    assign_third_slots,
)
from .schemas import Match, Team
from .simulator import DEFAULT_PARAMS, ModelParams, expected_goals

# Fases acumuladas para a tabela de confiança (em ordem de profundidade).
_STAGES = ("qualify", "r16", "qf", "sf", "final", "champion")


@dataclass
class MonteCarloResult:
    """Probabilidades agregadas por seleção (em %)."""

    n_sims: int
    probabilities: dict[str, dict[str, float]] = field(default_factory=dict)

    def table(self, teams: dict[str, Team], top: int = 16) -> list[tuple[str, dict]]:
        """Seleções ordenadas por probabilidade de título (desc)."""
        rows = sorted(
            self.probabilities.items(),
            key=lambda kv: kv[1]["champion"],
            reverse=True,
        )
        return rows[:top]


# ---------------------------------------------------------------------------
# Amostragem de jogos
# ---------------------------------------------------------------------------
def _effective_rating(team: Team, params: ModelParams, host_bonus: bool) -> float:
    bonus = params.home_advantage if (team.is_host and host_bonus) else 0.0
    return team.elo + team.form_modifier + bonus


def _sample_knockout_winner(
    home: Team,
    away: Team,
    params: ModelParams,
    rng: np.random.Generator,
) -> tuple[str, str]:
    """Amostra um jogo eliminatório; devolve (vencedor_id, perdedor_id)."""
    lam_h, lam_a = expected_goals(home, away, params)
    gh, ga = rng.poisson(lam_h), rng.poisson(lam_a)
    if gh > ga:
        return home.team_id, away.team_id
    if ga > gh:
        return away.team_id, home.team_id
    # Empate -> pênaltis: probabilidade logística sobre a diferença de Elo.
    r_home = _effective_rating(home, params, host_bonus=True)
    r_away = _effective_rating(away, params, host_bonus=False)
    p_home = 1.0 / (1.0 + 10 ** ((r_away - r_home) / params.elo_divisor))
    if rng.random() < p_home:
        return home.team_id, away.team_id
    return away.team_id, home.team_id


# ---------------------------------------------------------------------------
# Núcleo: uma simulação completa do torneio
# ---------------------------------------------------------------------------
@dataclass
class _Third:
    """Registo mínimo de um terceiro colocado para atribuição de slots."""

    team_id: str
    group: str


def _rank_group(
    members: list[str],
    pts: dict[str, int],
    gd: dict[str, int],
    gf: dict[str, int],
    teams: dict[str, Team],
) -> list[str]:
    """Ordena um grupo: pontos -> SG -> GP -> Elo (desempate determinístico)."""
    return sorted(
        members,
        key=lambda t: (pts[t], gd[t], gf[t], teams[t].elo),
        reverse=True,
    )


def _assign_thirds_robust(thirds: list[_Third]) -> dict[str, str]:
    """Atribui terceiros aos slots; usa fallback se a combinação não casar."""
    try:
        return assign_third_slots(thirds)
    except ValueError:
        slots = [away for _, _, away in ROUND_OF_32 if away.startswith("3")]
        return {slot: t.team_id for slot, t in zip(slots, thirds)}


def simulate_once(
    teams: dict[str, Team],
    group_fixtures: list[tuple[str, str, str]],
    group_lambdas: np.ndarray,
    rng: np.random.Generator,
    params: ModelParams,
    reached: dict[str, dict[str, int]],
    fixed_results: list[tuple[int, int] | None] | None = None,
) -> None:
    """Simula um torneio e incrementa `reached` para cada seleção/fase.

    `fixed_results` permite condicionar a simulação aos jogos JÁ disputados:
    para cada índice com um placar fixo, usa-se esse resultado em vez de amostrar
    (previsão 'viva' que parte do estado real do torneio).
    """
    n = len(group_fixtures)
    gh = rng.poisson(group_lambdas[:, 0])
    ga = rng.poisson(group_lambdas[:, 1])

    pts: dict[str, int] = defaultdict(int)
    gd: dict[str, int] = defaultdict(int)
    gf: dict[str, int] = defaultdict(int)
    members: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for i in range(n):
        group, h, a = group_fixtures[i]
        for tid in (h, a):
            if tid not in seen:
                seen.add(tid)
                members[group].append(tid)
        if fixed_results is not None and fixed_results[i] is not None:
            hh, aa = fixed_results[i]   # jogo já disputado: usa o placar real
        else:
            hh, aa = int(gh[i]), int(ga[i])
        gf[h] += hh; gf[a] += aa
        gd[h] += hh - aa; gd[a] += aa - hh
        if hh > aa:
            pts[h] += 3
        elif aa > hh:
            pts[a] += 3
        else:
            pts[h] += 1; pts[a] += 1

    winners: dict[str, str] = {}
    runners: dict[str, str] = {}
    thirds_pool: list[_Third] = []
    for group, mem in members.items():
        ranked = _rank_group(mem, pts, gd, gf, teams)
        winners[group] = ranked[0]
        runners[group] = ranked[1]
        thirds_pool.append(_Third(ranked[2], group))

    # 8 melhores terceiros.
    thirds_pool.sort(
        key=lambda r: (pts[r.team_id], gd[r.team_id], gf[r.team_id], teams[r.team_id].elo),
        reverse=True,
    )
    best_thirds = thirds_pool[:8]
    third_slots = _assign_thirds_robust(best_thirds)

    # Todas as 32 seleções que avançaram.
    qualified = set(winners.values()) | set(runners.values()) | {t.team_id for t in best_thirds}
    for tid in qualified:
        reached[tid]["qualify"] += 1

    def resolve(slot: str) -> str:
        if slot.startswith("1"):
            return winners[slot[1]]
        if slot.startswith("2"):
            return runners[slot[1]]
        return third_slots[slot]

    # 32-avos: o vencedor alcança as oitavas ("r16").
    outcomes: dict[int, tuple[str, str]] = {}
    for num, hs, as_ in ROUND_OF_32:
        w, l = _sample_knockout_winner(teams[resolve(hs)], teams[resolve(as_)], params, rng)
        outcomes[num] = (w, l)
        reached[w]["r16"] += 1

    def ref(r: str) -> str:
        num = int(r[1:])
        return outcomes[num][0] if r[0] == "W" else outcomes[num][1]

    # Cada ronda: vencer leva à fase seguinte (oitavas->quartas->semis->final).
    for struct, label in (
        (ROUND_OF_16, "qf"),
        (QUARTER_FINALS, "sf"),
        (SEMI_FINALS, "final"),
    ):
        for num, hr, ar in struct:
            w, l = _sample_knockout_winner(teams[ref(hr)], teams[ref(ar)], params, rng)
            outcomes[num] = (w, l)
            reached[w][label] += 1  # os 2 vencedores das semis = os 2 finalistas

    # Final: o vencedor é campeão (ambos finalistas já contam em "final").
    num, hr, ar = FINAL
    w, l = _sample_knockout_winner(teams[ref(hr)], teams[ref(ar)], params, rng)
    reached[w]["champion"] += 1


# ---------------------------------------------------------------------------
# Driver público
# ---------------------------------------------------------------------------
def run_monte_carlo(
    teams: dict[str, Team] | None = None,
    n_sims: int = 10_000,
    params: ModelParams = DEFAULT_PARAMS,
    seed: int | None = None,
    group_matches: list[Match] | None = None,
) -> MonteCarloResult:
    """Corre `n_sims` torneios completos e devolve probabilidades por fase.

    Se `group_matches` for fornecido, a simulação é CONDICIONADA aos jogos de
    grupo já disputados (com `real_score`): esses resultados são fixados e só os
    jogos por disputar são amostrados — é a previsão 'viva' a partir do estado
    real do torneio. Sem ele, estima o cenário pré-torneio.
    """
    teams = teams or build_teams()
    rng = np.random.default_rng(seed)

    source = group_matches if group_matches is not None else build_group_stage_matches()
    fixtures: list[tuple[str, str, str]] = [
        (m.group, m.home_team, m.away_team) for m in source
    ]
    fixed_results: list[tuple[int, int] | None] = [
        (m.real_score.home_goals, m.real_score.away_goals)
        if m.real_score is not None else None
        for m in source
    ]
    lambdas = np.array(
        [expected_goals(teams[h], teams[a], params) for _, h, a in fixtures]
    )

    reached: dict[str, dict[str, int]] = {
        tid: {stage: 0 for stage in _STAGES} for tid in teams
    }
    for _ in range(n_sims):
        simulate_once(teams, fixtures, lambdas, rng, params, reached, fixed_results)

    probabilities = {
        tid: {stage: round(100 * counts[stage] / n_sims, 1) for stage in _STAGES}
        for tid, counts in reached.items()
    }
    return MonteCarloResult(n_sims=n_sims, probabilities=probabilities)


# ---------------------------------------------------------------------------
# Demo: python -m src.model.montecarlo
# ---------------------------------------------------------------------------
def _demo() -> None:  # pragma: no cover
    teams = build_teams()
    n = 10_000
    print(f"\nA simular {n} torneios completos (Monte Carlo)...")
    result = run_monte_carlo(teams, n_sims=n, seed=42)

    print("\n=== PROBABILIDADES POR FASE (%) — top 16 ===\n")
    hdr = f"{'Seleção':<22}{'Avança':>8}{'Oitavas':>9}{'Quartas':>9}{'Semis':>8}{'Final':>8}{'Título':>8}"
    print(hdr); print("-" * len(hdr))
    for tid, p in result.table(teams, top=16):
        print(
            f"{teams[tid].name:<22}{p['qualify']:>7.1f}%{p['r16']:>8.1f}%"
            f"{p['qf']:>8.1f}%{p['sf']:>7.1f}%{p['final']:>7.1f}%{p['champion']:>7.1f}%"
        )
    print()


if __name__ == "__main__":  # pragma: no cover
    _demo()
