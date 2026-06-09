"""Testes de sanidade do simulador estatístico (Passo 2)."""

from __future__ import annotations

from src.data.ratings import build_first_round_matches, build_teams
from src.model.schemas import MatchStatus, Outcome, Phase
from src.model.simulator import (
    expected_goals,
    predict_first_round,
    predict_match,
    score_matrix,
)

teams = build_teams()


def test_score_matrix_is_normalized():
    lh, la = expected_goals(teams["BRA"], teams["KSA"])
    matrix = score_matrix(lh, la)
    total = sum(p for row in matrix for p in row)
    assert abs(total - 1.0) < 1e-9


def test_outcome_probabilities_sum_to_one():
    pred = predict_match(teams["ARG"], teams["RSA"])
    assert abs(pred.prob_home + pred.prob_draw + pred.prob_away - 1.0) < 1e-9


def test_stronger_team_is_favored():
    # Argentina (Elo alto) vs África do Sul (Elo baixo) em casa.
    pred = predict_match(teams["ARG"], teams["RSA"])
    assert pred.prob_home > pred.prob_away
    assert pred.expected_winner == "ARG"


def test_predicted_score_matches_winner():
    """Placar previsto deve ser coerente com o vencedor previsto."""
    pred = predict_match(teams["ESP"], teams["GHA"])
    if pred.expected_winner == "ESP":
        assert pred.predicted_score.outcome is Outcome.HOME
    elif pred.expected_winner == "GHA":
        assert pred.predicted_score.outcome is Outcome.AWAY
    else:
        assert pred.predicted_score.outcome is Outcome.DRAW


def test_knockout_never_predicts_draw():
    pred = predict_match(teams["FRA"], teams["MAR"], knockout=True)
    assert pred.expected_winner is not None
    assert pred.predicted_score.outcome is not Outcome.DRAW


def test_confidence_in_valid_range():
    pred = predict_match(teams["GER"], teams["HAI"])
    assert 0.0 <= pred.confidence_level <= 100.0
    # Confiança = probabilidade do desfecho previsto (em %).
    assert pred.confidence_level >= 33.0  # acima do acaso de 3 vias


def test_first_round_has_24_matches():
    matches = build_first_round_matches()
    assert len(matches) == 24
    assert all(m.phase is Phase.GROUP_STAGE and m.matchday == 1 for m in matches)


def test_predict_first_round_populates_predictions():
    matches = predict_first_round(build_first_round_matches(), teams)
    assert all(m.prediction is not None for m in matches)
    assert all(m.status is MatchStatus.PREDICTED for m in matches)


def test_set_real_score_marks_finished():
    match = build_first_round_matches()[0]
    match.set_real_score(2, 0)
    assert match.status is MatchStatus.FINISHED
    assert match.is_finished
    assert match.actual_winner == match.home_team
