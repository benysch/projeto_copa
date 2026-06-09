"""Testes do Passo 4: simulação de Monte Carlo."""

from __future__ import annotations

from src.data.ratings import build_teams
from src.model.montecarlo import _STAGES, run_monte_carlo

teams = build_teams()
N = 300


def test_probabilities_for_all_teams():
    result = run_monte_carlo(teams, n_sims=N, seed=1)
    assert len(result.probabilities) == len(teams)
    assert all(set(p) == set(_STAGES) for p in result.probabilities.values())


def test_stage_probabilities_are_monotonic():
    """Para cada seleção: avançar >= oitavas >= quartas >= semis >= final >= título."""
    result = run_monte_carlo(teams, n_sims=N, seed=2)
    for p in result.probabilities.values():
        seq = [p[s] for s in _STAGES]
        assert seq == sorted(seq, reverse=True)


def test_aggregate_invariants():
    """Por simulação: 32 avançam, 1 campeão, 2 finalistas, 4 semifinalistas..."""
    result = run_monte_carlo(teams, n_sims=N, seed=3)
    total = lambda stage: sum(p[stage] for p in result.probabilities.values())
    # Tolerância de arredondamento: ~48 valores arredondados a 0.1 (±~2.4).
    tol = 3.0
    assert abs(total("qualify") - 3200.0) < tol   # 32 por torneio
    assert abs(total("r16") - 1600.0) < tol        # 16
    assert abs(total("qf") - 800.0) < tol          # 8
    assert abs(total("sf") - 400.0) < tol          # 4
    assert abs(total("final") - 200.0) < tol       # 2
    assert abs(total("champion") - 100.0) < tol    # 1


def test_seed_is_reproducible():
    a = run_monte_carlo(teams, n_sims=N, seed=7)
    b = run_monte_carlo(teams, n_sims=N, seed=7)
    assert a.probabilities == b.probabilities


def test_stronger_team_has_higher_title_odds():
    result = run_monte_carlo(teams, n_sims=2000, seed=11)
    p = result.probabilities
    # Espanha (Elo mais alto) deve ter mais chances de título que Gana.
    assert p["ESP"]["champion"] > p["GHA"]["champion"]
