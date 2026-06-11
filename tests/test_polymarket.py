"""Testes da integração Polymarket (payloads injetados — sem rede)."""

from __future__ import annotations

import json

import pytest

from src.data.polymarket import (
    _NAME_TO_ID,
    implied_title_probabilities,
    parse_title_prices,
)
from src.data.ratings import build_teams
from src.service.engine import PredictionEngine


def _market(name: str, price: float) -> dict:
    return {
        "question": f"Will {name} win the 2026 FIFA World Cup?",
        "outcomePrices": json.dumps([str(price), str(1 - price)]),
    }


FAKE_EVENT = {
    "markets": [
        _market("Spain", 0.17),
        _market("France", 0.16),
        _market("Brazil", 0.09),
        _market("Czech Republic", 0.002),     # variante de grafia
        _market("USA", 0.03),
        _market("Italy", 0.01),               # não classificada: ignorada
        {"question": "Will the final go to penalties?", "outcomePrices": "[\"0.3\",\"0.7\"]"},
    ]
}


def test_name_map_covers_all_48_teams():
    mapped = set(_NAME_TO_ID.values())
    assert set(build_teams()) <= mapped


def test_parse_extracts_known_teams_and_ignores_others():
    prices = parse_title_prices(FAKE_EVENT)
    assert prices["ESP"] == 0.17
    assert prices["CZE"] == 0.002
    assert "ITA" not in prices  # Itália não está no sorteio
    assert len(prices) == 5


def test_implied_probabilities_remove_vig():
    probs, vig = implied_title_probabilities(FAKE_EVENT)
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    raw = 0.17 + 0.16 + 0.09 + 0.002 + 0.03
    assert vig == round((raw - 1.0) * 100, 1)
    assert probs["ESP"] == pytest.approx(0.17 / raw)


def test_market_comparison_blends_and_ranks():
    eng = PredictionEngine()
    market = {"ESP": 0.40, "FRA": 0.30, "BRA": 0.20, "USA": 0.10}
    out = eng.market_comparison(
        n_sims=300, seed=1, market_probs=market, market_vig_pct=4.0
    )
    rows = {r["team_id"]: r for r in out["teams"]}
    assert set(rows) == set(market)
    esp = rows["ESP"]
    assert esp["edge_pp"] == pytest.approx(
        esp["model_pct"] - esp["market_pct"], abs=0.11
    )
    # Blend fica entre modelo e mercado (pool logarítmico).
    lo, hi = sorted((esp["model_pct"], esp["market_pct"]))
    assert lo - 5 <= esp["blend_pct"] <= hi + 5
    blend_total = sum(r["blend_pct"] for r in out["teams"])
    assert blend_total == pytest.approx(100.0, abs=0.5)


def test_blend_weight_extremes():
    eng = PredictionEngine()
    market = {"ESP": 0.50, "FRA": 0.50}
    only_market = eng.market_comparison(
        n_sims=200, seed=2, blend_weight=0.0,
        market_probs=market, market_vig_pct=0.0,
    )
    for r in only_market["teams"]:
        assert r["blend_pct"] == pytest.approx(r["market_pct"], abs=0.1)
    with pytest.raises(ValueError):
        eng.market_comparison(blend_weight=1.5, market_probs=market)
