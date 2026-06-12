"""Testes do otimizador de palpites para bolões (src/model/bolao.py).

Os casos das funções de pontos vêm DIRETAMENTE dos exemplos do regulamento da
Copa Pragma (PDF bolaoai) e das capturas do bolão do app (IMG_1884–1886).
"""

import pytest

from src.model import bolao
from src.model.schemas import Phase
from src.model.simulator import DEFAULT_PARAMS, expected_goals, score_matrix
from src.service.engine import PredictionEngine


# ---------------------------------------------------------------------------
# Função de pontos — Copa Pragma (exemplos do PDF; jogo de ref.: BRA 3x1 ARG)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "pred, actual, pts",
    [
        ((3, 1), (3, 1), 2),  # placar exato
        ((1, 0), (3, 1), 1),  # só vencedor
        ((1, 1), (2, 2), 1),  # empate sem exato
        ((1, 3), (3, 1), 0),  # errou
    ],
)
def test_pragma_base_points(pred, actual, pts):
    assert bolao.pragma_base_points(*pred, *actual) == pts


# ---------------------------------------------------------------------------
# Função de pontos — bolão do app (exemplos das capturas)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "pred, actual, pts",
    [
        ((2, 1), (2, 1), 6),  # placar exato
        ((2, 0), (2, 1), 4),  # vencedor + gols de um time
        ((1, 0), (3, 1), 3),  # vencedor sem placar exato
        ((2, 1), (0, 1), 1),  # gols de um time sem vencedor
        ((1, 1), (1, 1), 6),  # empate exato
        ((0, 0), (2, 2), 3),  # empate sem placar
        ((2, 1), (2, 2), 1),  # não-empate, mas gols da casa corretos
        ((1, 3), (3, 1), 0),  # nada
    ],
)
def test_app_base_points(pred, actual, pts):
    assert bolao.app_base_points(*pred, *actual) == pts


def test_app_penalty_table():
    """Tabela do mata-mata: 9 / 6 / 6 / 3 conforme as capturas."""
    # Grade degenerada: o jogo termina 1-1 com certeza; pênaltis ~proporcionais
    # (grade simétrica => 50/50).
    n = DEFAULT_PARAMS.max_goals + 1
    matrix = [[0.0] * n for _ in range(n)]
    matrix[1][1] = 1.0
    p_tb = bolao.tiebreak_probability(matrix)
    assert p_tb == 0.5

    # Empate exato + pênaltis: 6 + 3·P(acertar pênaltis) = 6 + 1.5
    ev = bolao.expected_points_app(matrix, (1, 1), knockout=True, penalty_pick_home=True)
    assert ev == pytest.approx(7.5)
    # Empate sem exato: 3 + 3·0.5
    ev = bolao.expected_points_app(matrix, (0, 0), knockout=True, penalty_pick_home=True)
    assert ev == pytest.approx(4.5)
    # Palpite decisivo nunca pontua pênaltis: só "gols de um time" (away=1)
    ev = bolao.expected_points_app(matrix, (2, 1), knockout=True)
    assert ev == pytest.approx(1.0)


def test_app_draw_pick_requires_penalty_winner_in_knockout():
    n = DEFAULT_PARAMS.max_goals + 1
    matrix = [[1.0 / n**2] * n for _ in range(n)]
    with pytest.raises(ValueError):
        bolao.expected_points_app(matrix, (1, 1), knockout=True)


# ---------------------------------------------------------------------------
# E[pontos] Pragma — multiplicador e bónus de avanço
# ---------------------------------------------------------------------------
def _real_matrix(elo_home=2000.0, elo_away=1900.0):
    from src.model.schemas import Team

    home = Team(team_id="AAA", name="Casa", elo=elo_home)
    away = Team(team_id="BBB", name="Fora", elo=elo_away)
    return score_matrix(*expected_goals(home, away, DEFAULT_PARAMS), DEFAULT_PARAMS)


def test_pragma_knockout_includes_multiplier_and_bonus():
    matrix = _real_matrix()
    ev_group = bolao.expected_points_pragma(matrix, (2, 1), Phase.GROUP_STAGE)
    ev_final = bolao.expected_points_pragma(matrix, (2, 1), Phase.FINAL)
    # Final: base×10 + 10·P(casa avança) > base×1.
    assert ev_final > ev_group * 9
    # O bónus usa P(avançar), que inclui a massa de empate redistribuída.
    p_home, p_draw, p_away = bolao._draw_split(matrix)
    p_adv = p_home + p_draw * bolao.tiebreak_probability(matrix)
    assert ev_final == pytest.approx(ev_group * 10 + 10 * p_adv)


def test_pragma_knockout_rejects_draw_picks():
    matrix = _real_matrix()
    picks = bolao.best_picks(matrix, "pragma", Phase.ROUND_OF_16, top=200)
    assert all(p["score"][0] != p["score"][1] for p in picks)
    assert all("advancer_home" in p for p in picks)


# ---------------------------------------------------------------------------
# Otimizador — propriedades gerais
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ruleset", ["pragma", "app"])
@pytest.mark.parametrize("phase", [Phase.GROUP_STAGE, Phase.ROUND_OF_32])
def test_best_pick_beats_or_ties_modal(ruleset, phase):
    matrix = _real_matrix()
    flat = [((i, j), p) for i, row in enumerate(matrix) for j, p in enumerate(row)]
    modal = max(flat, key=lambda t: t[1])[0]
    best = bolao.best_picks(matrix, ruleset, phase, top=1)[0]
    ev_modal = bolao.evaluate_pick(matrix, modal, ruleset, phase)
    assert best["expected_points"] >= ev_modal - 1e-12


def test_best_picks_sorted_and_sized():
    matrix = _real_matrix()
    picks = bolao.best_picks(matrix, "app", Phase.GROUP_STAGE, top=5)
    assert len(picks) == 5
    evs = [p["expected_points"] for p in picks]
    assert evs == sorted(evs, reverse=True)


def test_unknown_ruleset_raises():
    matrix = _real_matrix()
    with pytest.raises(ValueError):
        bolao.best_picks(matrix, "loteria", Phase.GROUP_STAGE)


# ---------------------------------------------------------------------------
# Integração — engine.bolao_picks
# ---------------------------------------------------------------------------
def test_engine_bolao_picks_group_stage():
    engine = PredictionEngine()
    picks = engine.bolao_picks("app", Phase.GROUP_STAGE, top=3, matchday=1)
    assert len(picks) == 24
    for entry in picks:
        assert entry["matchday"] == 1
        assert len(entry["picks"]) == 3
        assert "modal_expected_points" in entry
        assert entry["ev_gain_vs_modal"] >= -1e-9


def test_engine_bolao_picks_knockout_pragma():
    engine = PredictionEngine()
    picks = engine.bolao_picks("pragma", Phase.ROUND_OF_32, top=2)
    assert len(picks) == 16
    for entry in picks:
        for p in entry["picks"]:
            assert "advancer" in p
            h, a = p["score"].split("-")
            assert h != a


def test_engine_bolao_picks_skips_finished_matches():
    engine = PredictionEngine()
    first = engine.get_phase(Phase.GROUP_STAGE)[0]
    engine.update_real_score(first.match_id, 2, 0)
    picks = engine.bolao_picks("pragma", Phase.GROUP_STAGE)
    entry = next(e for e in picks if e["match_id"] == first.match_id)
    assert entry["status"] == "finished"
    assert "picks" not in entry
    assert entry["real_score"] == "2-0"
