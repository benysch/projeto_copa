"""Teste do benchmark externo (saltado se o SportIQ não estiver instalado)."""

from __future__ import annotations

import pytest

# Dependência opcional: salta se o motor de benchmark não estiver presente.
pytest.importorskip("sportiq", reason="instale requirements-dev.txt para o benchmark")

from benchmarks.compare_sportiq import run_benchmark


def test_engines_agree_within_tolerance():
    """Nos mesmos inputs, os dois motores devem concordar de perto (~ruído MC)."""
    result = run_benchmark(n_sims=4000, seed=42)
    # Diferença média absoluta pequena valida a nossa implementação.
    assert result["mean_abs_diff"] < 2.0   # pontos percentuais
    assert result["max_abs_diff"] < 6.0


def test_top_favorites_consistent():
    """Os favoritos ao título coincidem entre os dois motores (top-3 sobrepõe-se).

    Não se exige o mesmo nº 1 (os 2-3 primeiros estão a poucos pp de distância,
    dentro do ruído de Monte Carlo), mas sim que o favorito de um esteja no
    top-3 do outro.
    """
    result = run_benchmark(n_sims=4000, seed=42)
    our_top3 = sorted(result["ours"], key=result["ours"].get, reverse=True)[:3]
    their_top3 = sorted(result["theirs"], key=result["theirs"].get, reverse=True)[:3]
    assert our_top3[0] in their_top3
    assert their_top3[0] in our_top3
