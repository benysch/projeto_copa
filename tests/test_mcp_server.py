"""Testes do servidor MCP (Passo 5), via cliente in-memory do fastmcp."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from src.mcp_server import engine, mcp


def _call(tool: str, args: dict | None = None):
    async def run():
        async with Client(mcp) as c:
            res = await c.call_tool(tool, args or {})
            return res.data
    return asyncio.run(run())


def setup_function(_):
    """Estado limpo antes de cada teste (o motor MCP é global)."""
    engine.reset()


def test_all_tools_registered():
    async def run():
        async with Client(mcp) as c:
            return {t.name for t in await c.list_tools()}
    names = asyncio.run(run())
    assert {
        "get_phase_predictions", "update_real_score", "get_match",
        "get_group_standings", "get_title_probabilities", "resolve_playoff",
        "list_phases", "sync_results", "get_elo_ratings", "simulate_tournament",
    } <= names


def test_sync_results_reports_state():
    out = _call("sync_results")
    assert out["provider"] == "StaticProvider"
    assert out["finished_group_matches"] == 0
    _call("update_real_score", {"match_id": "A11", "home_goals": 2, "away_goals": 0})
    out = _call("sync_results")
    assert out["finished_group_matches"] == 1


def test_get_elo_ratings_shows_live_delta():
    out = _call("get_elo_ratings", {"top": 48})
    assert out["recalibration_enabled"] is True
    assert all(t["delta_vs_base"] == 0 for t in out["teams"])
    _call("update_real_score", {"match_id": "H32", "home_goals": 4, "away_goals": 0})
    out = _call("get_elo_ratings", {"top": 48})
    uru = next(t for t in out["teams"] if t["id"] == "URU")
    esp = next(t for t in out["teams"] if t["id"] == "ESP")
    assert uru["delta_vs_base"] > 0 > esp["delta_vs_base"]


def test_get_phase_predictions_final():
    data = _call("get_phase_predictions", {"phase_name": "final"})
    assert data["phase"] == "final"
    assert data["match_count"] == 1
    pred = data["matches"][0]["prediction"]
    assert pred["expected_winner"] is not None
    assert 0 <= pred["confidence_level"] <= 100


def test_phase_alias_resolution():
    by_alias = _call("get_phase_predictions", {"phase_name": "oitavas"})
    by_value = _call("get_phase_predictions", {"phase_name": "round_of_16"})
    assert by_alias["phase"] == by_value["phase"] == "round_of_16"
    assert by_alias["match_count"] == 8


def test_group_stage_has_72():
    data = _call("get_phase_predictions", {"phase_name": "grupos"})
    assert data["match_count"] == 72


def test_matchday_filter():
    data = _call("get_phase_predictions", {"phase_name": "grupos", "matchday": 1})
    assert data["match_count"] == 24
    assert all(m["matchday"] == 1 for m in data["matches"])


def test_invalid_matchday_errors():
    import pytest
    with pytest.raises(Exception):
        _call("get_phase_predictions", {"phase_name": "grupos", "matchday": 9})


def test_update_real_score_recalculates():
    standings = _call("get_group_standings", {"group": "H"})
    assert standings["table"][0]["team"]["id"] == "ESP"
    _call("update_real_score", {"match_id": "H11", "home_goals": 0, "away_goals": 4})
    after = _call("get_group_standings", {"group": "H"})
    uru = next(r for r in after["table"] if r["team"]["id"] == "URU")
    assert uru["goals_for"] >= 4


def test_resolve_playoff_adjusts_team():
    """Sem placeholders restantes, a ferramenta serve para corrigir nome/Elo."""
    out = _call("resolve_playoff", {"slot_id": "BIH", "team_name": "Bósnia", "elo": 1610})
    assert out["resolved"] == "BIH"
    grp = _call("get_group_standings", {"group": "B"})
    names = [r["team"]["name"] for r in grp["table"]]
    assert "Bósnia" in names


def test_get_matches_by_date_window():
    out = _call("get_matches_by_date", {"start_date": "2026-06-11", "days": 2})
    # Janela 11–12/06 (UTC): abertura A11, depois A12 e B11.
    ids = [m["match_id"] for m in out["matches"]]
    assert ids[0] == "A11"
    assert out["match_count"] == len(ids) >= 3
    assert all(m["kickoff_utc"] for m in out["matches"])


def test_get_matches_by_date_defaults_to_today():
    out = _call("get_matches_by_date", {})
    assert out["days"] == 5
    assert out["start_date"]  # default = hoje (UTC)


def test_simulate_tournament_returns_full_scenario():
    sc = _call("simulate_tournament", {"seed": 42})
    assert sc["kind"] == "sampled_scenario"
    assert len(sc["group_stage"]["matches"]) == 72
    assert sc["champion"]["id"] in engine.teams
    # Reprodutível com a mesma seed.
    assert _call("simulate_tournament", {"seed": 42}) == sc


def test_simulate_tournament_respects_real_results():
    _call("update_real_score", {"match_id": "A11", "home_goals": 3, "away_goals": 0})
    sc = _call("simulate_tournament", {"seed": 5})
    entry = next(m for m in sc["group_stage"]["matches"] if m["match_id"] == "A11")
    assert entry["fixed"] is True
    assert entry["score"] == "3-0"


def test_get_match_includes_top_scorelines():
    out = _call("get_match", {"match_id": "A11"})
    lines = out["prediction"]["top_scorelines"]
    assert lines and all(0 <= s["probability_pct"] <= 100 for s in lines)


def test_list_phases_counts():
    data = _call("list_phases")
    counts = {p["name"]: p["match_count"] for p in data["phases"]}
    assert counts["round_of_32"] == 16
    assert counts["final"] == 1


def test_unknown_phase_errors():
    import pytest
    with pytest.raises(Exception):
        _call("get_phase_predictions", {"phase_name": "inexistente"})
