"""Testes do cenário amostrado (1 sorteio de Monte Carlo, jogo a jogo)."""

from __future__ import annotations

from src.data.ratings import build_group_stage_matches, build_teams
from src.model.scenario import sample_scenario

teams = build_teams()


def _scenario(seed: int, matches=None):
    return sample_scenario(teams, matches or build_group_stage_matches(), seed=seed)


def test_structure_and_invariants():
    sc = _scenario(1)
    assert sc["kind"] == "sampled_scenario"
    assert len(sc["group_stage"]["matches"]) == 72
    assert len(sc["group_stage"]["standings"]) == 12
    assert all(len(t) == 4 for t in sc["group_stage"]["standings"].values())
    ko = sc["knockout"]
    assert [len(ko[k]) for k in (
        "round_of_32", "round_of_16", "quarter_finals",
        "semi_finals", "third_place", "final",
    )] == [16, 8, 4, 2, 1, 1]
    # Todo jogo eliminatório tem vencedor; o campeão é o vencedor da final.
    assert all(m["winner"] for r in ko.values() for m in r)
    assert sc["champion"] == ko["final"][0]["winner"]
    assert sc["champion"]["id"] in teams


def test_seed_is_reproducible_and_seeds_differ():
    assert _scenario(7) == _scenario(7)
    a, b = _scenario(1), _scenario(2)
    assert a["group_stage"]["matches"] != b["group_stage"]["matches"]


def test_draws_appear_at_realistic_rate():
    """Empates na fase de grupos: ~15-25% historicamente; tolerância larga."""
    total = sum(_scenario(seed)["summary"]["group_draws"] for seed in range(10))
    assert 50 <= total <= 250  # de 720 jogos (10 cenários x 72)


def test_summary_counts_match_flags():
    sc = _scenario(3)
    all_matches = sc["group_stage"]["matches"] + [
        m for r in sc["knockout"].values() for m in r
    ]
    flagged = lambda f: sum(f in m.get("flags", ()) for m in all_matches)
    assert sc["summary"]["upsets"] == flagged("upset")
    assert sc["summary"]["goleadas"] == flagged("goleada")
    assert sc["summary"]["group_draws"] == sum(
        m["winner"] is None for m in sc["group_stage"]["matches"]
    )
    assert sc["summary"]["penalty_shootouts"] == sum(
        m["penalties"] for r in sc["knockout"].values() for m in r
    )


def test_fixed_results_are_respected():
    matches = build_group_stage_matches()
    target = matches[0]
    target.set_real_score(5, 0)
    sc = sample_scenario(teams, matches, seed=4)
    entry = next(
        m for m in sc["group_stage"]["matches"] if m["match_id"] == target.match_id
    )
    assert entry["fixed"] is True
    assert entry["score"] == "5-0"
    assert sc["summary"]["fixed_matches"] == 1
