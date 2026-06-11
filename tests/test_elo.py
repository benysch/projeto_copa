"""Testes da recalibração contínua de Elo (módulo + integração no engine)."""

from __future__ import annotations

from src.data.providers import StaticProvider
from src.model.elo import expected_result, goal_multiplier, rating_delta
from src.service.engine import PredictionEngine


# ---------------------------------------------------------------------------
# Fórmula
# ---------------------------------------------------------------------------
def test_expected_result_symmetry():
    assert abs(expected_result(1800, 1800) - 0.5) < 1e-9
    assert expected_result(1900, 1700) + expected_result(1700, 1900) == 1.0


def test_goal_multiplier_steps():
    assert goal_multiplier(0) == 1.0
    assert goal_multiplier(1) == 1.0
    assert goal_multiplier(-2) == 1.5
    assert goal_multiplier(3) == (11 + 3) / 8


def test_upset_moves_more_points_than_expected_win():
    favorite_wins = rating_delta(1900, 1600, 2, 0)
    upset = rating_delta(1900, 1600, 0, 2)  # delta da casa (favorita) ao perder
    assert favorite_wins > 0
    assert upset < 0
    assert abs(upset) > abs(favorite_wins)


def test_draw_drains_favorite():
    delta = rating_delta(1900, 1600, 1, 1)
    assert delta < 0  # favorita 'perde' rating ao empatar


# ---------------------------------------------------------------------------
# Integração no engine
# ---------------------------------------------------------------------------
def test_real_result_recalibrates_elo():
    eng = PredictionEngine(StaticProvider())
    base_esp, base_uru = eng.teams["ESP"].elo, eng.teams["URU"].elo
    eng.update_real_score("H32", 4, 0)  # goleada do Uruguai sobre a Espanha (H32 = URU x ESP)
    assert eng.teams["URU"].elo > base_uru
    assert eng.teams["ESP"].elo < base_esp
    # Transferência simétrica de pontos.
    total = eng.teams["ESP"].elo + eng.teams["URU"].elo
    assert abs(total - (base_esp + base_uru)) < 1e-6


def test_recalibration_is_idempotent_across_refreshes():
    eng = PredictionEngine(StaticProvider())
    eng.update_real_score("H32", 4, 0)
    after_first = eng.teams["URU"].elo
    eng.refresh()
    eng.refresh()
    assert abs(eng.teams["URU"].elo - after_first) < 1e-9


def test_correcting_result_leaves_no_residue():
    eng = PredictionEngine(StaticProvider())
    base = eng.teams["URU"].elo
    eng.update_real_score("H32", 4, 0)
    eng.update_real_score("H32", 1, 1)  # corrige: era empate
    boosted = base < eng.teams["URU"].elo
    assert boosted  # empate com a Espanha ainda rende pontos ao URU
    delta_draw = eng.teams["URU"].elo - base
    # Reinsere a goleada e volta ao empate: delta idêntico (sem resíduo).
    eng.update_real_score("H32", 4, 0)
    eng.update_real_score("H32", 1, 1)
    assert abs((eng.teams["URU"].elo - base) - delta_draw) < 1e-9


def test_reset_restores_base_ratings():
    eng = PredictionEngine(StaticProvider())
    base = eng.teams["ESP"].elo
    eng.update_real_score("H32", 4, 0)
    assert eng.teams["ESP"].elo != base
    eng.reset()
    assert eng.teams["ESP"].elo == base
    assert eng.elo_delta("ESP") == 0.0


def test_knockout_result_recalibrates_elo():
    eng = PredictionEngine(StaticProvider())
    m73 = eng._find_match("m73")
    home, away = m73.home_team, m73.away_team
    base_home = eng.teams[home].elo
    eng.update_real_score("m73", 3, 0)
    assert eng.teams[home].elo > base_home
    assert eng.elo_delta(home) > 0 > eng.elo_delta(away)


def test_recalibration_can_be_disabled():
    eng = PredictionEngine(StaticProvider(), recalibrate_elo=False)
    base = eng.teams["URU"].elo
    eng.update_real_score("H32", 4, 0)
    assert eng.teams["URU"].elo == base
