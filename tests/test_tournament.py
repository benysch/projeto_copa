"""Testes do Passo 3: classificação, melhores terceiros e chaveamento."""

from __future__ import annotations

from src.data.ratings import build_group_stage_matches, build_teams
from src.model.bracket import (
    ROUND_OF_32,
    assign_third_slots,
    build_round_of_32,
    _third_slot_groups,
)
from src.model.schemas import Outcome, Phase
from src.model.standings import best_third_placed, compute_all_standings
from src.model.tournament import run_full_tournament

teams = build_teams()


def test_group_stage_has_72_matches():
    matches = build_group_stage_matches()
    assert len(matches) == 72
    # Cada seleção joga exactamente 3 jogos.
    counts: dict[str, int] = {}
    for m in matches:
        counts[m.home_team] = counts.get(m.home_team, 0) + 1
        counts[m.away_team] = counts.get(m.away_team, 0) + 1
    assert set(counts.values()) == {3}


def test_all_12_groups_classified():
    result = run_full_tournament(teams)
    assert len(result.standings.tables) == 12
    assert all(len(t) == 4 for t in result.standings.tables.values())


def test_strongest_team_wins_its_group():
    result = run_full_tournament(teams)
    # Espanha (maior Elo) deve vencer o grupo H.
    assert result.standings.position("H", 1).team_id == "ESP"


def test_exactly_8_best_thirds():
    result = run_full_tournament(teams)
    thirds = best_third_placed(result.standings, teams)
    assert len(thirds) == 8
    # Todos vêm de grupos distintos.
    assert len({r.group for r in thirds}) == 8


def test_third_slot_assignment_is_legal():
    result = run_full_tournament(teams)
    thirds = best_third_placed(result.standings, teams)
    assignment = assign_third_slots(thirds)
    third_team_to_group = {r.team_id: r.group for r in thirds}
    for slot, team_id in assignment.items():
        assert third_team_to_group[team_id] in _third_slot_groups(slot)
    # 8 slots, 8 seleções distintas.
    assert len(assignment) == 8
    assert len(set(assignment.values())) == 8


def test_round_of_32_has_16_real_matches():
    result = run_full_tournament(teams)
    r32 = result.phase(Phase.ROUND_OF_32)
    assert len(r32) == 16
    assert len(ROUND_OF_32) == 16
    # Todas as seleções concretas (sem slots por resolver) e distintas.
    participants = [m.home_team for m in r32] + [m.away_team for m in r32]
    assert len(participants) == 32
    assert len(set(participants)) == 32


def test_knockout_round_sizes():
    result = run_full_tournament(teams)
    assert len(result.phase(Phase.ROUND_OF_16)) == 8
    assert len(result.phase(Phase.QUARTER_FINALS)) == 4
    assert len(result.phase(Phase.SEMI_FINALS)) == 2
    assert len(result.phase(Phase.THIRD_PLACE)) == 1
    assert len(result.phase(Phase.FINAL)) == 1


def test_knockouts_never_draw():
    result = run_full_tournament(teams)
    for phase in (Phase.ROUND_OF_32, Phase.ROUND_OF_16, Phase.QUARTER_FINALS,
                  Phase.SEMI_FINALS, Phase.THIRD_PLACE, Phase.FINAL):
        for m in result.phase(phase):
            assert m.prediction.expected_winner is not None
            assert m.prediction.predicted_score.outcome is not Outcome.DRAW


def test_tournament_produces_champion():
    result = run_full_tournament(teams)
    assert result.champion is not None
    final = result.phase(Phase.FINAL)[0]
    assert result.champion in (final.home_team, final.away_team)
