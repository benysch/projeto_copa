"""Testes do alerta de equilíbrio (jogo sem favorito claro / empate provável)."""

from __future__ import annotations

from src.model.schemas import MatchPrediction, Phase, Score
from src.service.engine import PredictionEngine
from src.service.serializers import match_to_dict


def _pred(ph: float, pd: float, pa: float) -> MatchPrediction:
    return MatchPrediction(
        predicted_score=Score(home_goals=1, away_goals=0),
        expected_winner="AAA",
        confidence_level=round(max(ph, pd, pa) * 100, 1),
        prob_home=ph,
        prob_draw=pd,
        prob_away=pa,
    )


def test_low_confidence_is_balanced():
    assert _pred(0.36, 0.29, 0.35).is_balanced  # nenhum desfecho chega a 40%


def test_draw_close_to_leader_is_balanced():
    assert _pred(0.45, 0.41, 0.14).is_balanced  # empate a 4 p.p. do líder


def test_clear_favorite_is_not_balanced():
    assert not _pred(0.62, 0.22, 0.16).is_balanced


def test_serializer_flags_balanced_group_matches():
    eng = PredictionEngine()
    # A12 (Coreia do Sul x Tchéquia) é o jogo mais parelho da fase de grupos.
    d = match_to_dict(eng._find_match("A12"), eng.teams)
    assert "balanced" in d.get("flags", [])
    assert "equilibrado" in d["prediction"]["note"]
    # H21 (Espanha x Arábia Saudita) tem favorita clara.
    d = match_to_dict(eng._find_match("H21"), eng.teams)
    assert "balanced" not in d.get("flags", [])
    assert "note" not in d["prediction"]


def test_knockout_matches_never_flagged():
    """Nas eliminatórias o empate não é desfecho final — alerta não se aplica."""
    eng = PredictionEngine()
    for phase, matches in eng.rounds.items():
        assert phase is not Phase.GROUP_STAGE
        for m in matches:
            d = match_to_dict(m, eng.teams)
            assert "balanced" not in d.get("flags", [])
