"""Cenário amostrado do torneio — uma realização plausível, não a mais provável.

Enquanto `predict_match` devolve a MODA de cada jogo (o favorito vence, placar
1-0/2-1, quase nunca empate) e `run_monte_carlo` devolve PROBABILIDADES
agregadas, este módulo sorteia UM torneio inteiro da distribuição do modelo
(grade de Poisson com correção de Dixon-Coles — a mesma de `predict_match`).

Numa amostra, empates, zebras e goleadas aparecem na frequência estatística
esperada: é o "como uma Copa de verdade pode se desenrolar", em contraste com
o cenário modal em que todo favorito vence por placar magro.

Como em `run_monte_carlo`, o cenário é condicionado aos jogos de GRUPO já
disputados (placares reais ficam fixos); resultados reais das eliminatórias
não são fixados, pois o chaveamento amostrado pode diferir do real.
"""

from __future__ import annotations

import numpy as np

from .bracket import (
    FINAL,
    QUARTER_FINALS,
    ROUND_OF_16,
    ROUND_OF_32,
    SEMI_FINALS,
    THIRD_PLACE,
    assign_third_slots,
)
from .schemas import Match, Team
from .simulator import DEFAULT_PARAMS, ModelParams, expected_goals, score_matrix
from .standings import TeamRecord, best_third_placed, compute_all_standings

# Limiares dos marcadores narrativos do cenário.
UPSET_ELO_GAP = 100.0  # zebra: vencedor com >= 100 pts de Elo efetivo a menos
GOLEADA_MARGIN = 3     # goleada: vitória por 3+ gols de diferença


# ---------------------------------------------------------------------------
# Amostragem e marcadores
# ---------------------------------------------------------------------------
def _sample_score(
    home: Team,
    away: Team,
    params: ModelParams,
    rng: np.random.Generator,
) -> tuple[int, int]:
    """Sorteia um placar da grade Dixon-Coles (consistente com `predict_match`)."""
    lam_h, lam_a = expected_goals(home, away, params)
    matrix = np.asarray(score_matrix(lam_h, lam_a, params))
    idx = int(rng.choice(matrix.size, p=matrix.ravel()))
    i, j = divmod(idx, matrix.shape[1])
    return i, j


def _pair_ratings(home: Team, away: Team, params: ModelParams) -> tuple[float, float]:
    """Ratings efetivos (Elo + forma + vantagem de anfitriã da casa)."""
    home_bonus = params.home_advantage if home.is_host else 0.0
    return (
        home.elo + home.form_modifier + home_bonus,
        away.elo + away.form_modifier,
    )


def _flags(
    home: Team,
    away: Team,
    gh: int,
    ga: int,
    winner_id: str | None,
    params: ModelParams,
) -> list[str]:
    """Marcadores narrativos: 'goleada' e 'upset' (zebra, inclui pênaltis)."""
    flags: list[str] = []
    if abs(gh - ga) >= GOLEADA_MARGIN:
        flags.append("goleada")
    if winner_id is not None:
        r_home, r_away = _pair_ratings(home, away, params)
        r_winner, r_loser = (
            (r_home, r_away) if winner_id == home.team_id else (r_away, r_home)
        )
        if r_loser - r_winner >= UPSET_ELO_GAP:
            flags.append("upset")
    return flags


def _assign_thirds(thirds: list[TeamRecord]) -> dict[str, str]:
    """Atribui terceiros aos slots; fallback se a combinação não casar."""
    try:
        return assign_third_slots(thirds, use_official=False)
    except ValueError:
        slots = [away for _, _, away in ROUND_OF_32 if away.startswith("3")]
        return {slot: r.team_id for slot, r in zip(slots, thirds)}


# ---------------------------------------------------------------------------
# Cenário completo
# ---------------------------------------------------------------------------
def sample_scenario(
    teams: dict[str, Team],
    group_matches: list[Match],
    params: ModelParams = DEFAULT_PARAMS,
    seed: int | None = None,
) -> dict:
    """Sorteia um torneio completo e devolve o cenário detalhado (dict).

    Jogos de grupo com `real_score` são fixados; os demais são amostrados.
    Repetir a chamada gera outro cenário; `seed` torna o sorteio reprodutível.
    """
    rng = np.random.default_rng(seed)

    def named(tid: str) -> dict:
        return {"id": tid, "name": teams[tid].name}

    summary = {
        "group_draws": 0,
        "upsets": 0,
        "goleadas": 0,
        "penalty_shootouts": 0,
        "fixed_matches": 0,
        "sampled_matches": 0,
    }

    def count_flags(flags: list[str]) -> None:
        summary["upsets"] += "upset" in flags
        summary["goleadas"] += "goleada" in flags

    # --- Fase de grupos: fixa os jogos disputados, amostra os restantes ----
    group_entries: list[dict] = []
    played: list[Match] = []  # clones com placar (real ou amostrado)
    for m in group_matches:
        home, away = teams[m.home_team], teams[m.away_team]
        if m.real_score is not None:
            gh, ga = m.real_score.home_goals, m.real_score.away_goals
            fixed = True
        else:
            gh, ga = _sample_score(home, away, params, rng)
            fixed = False
        clone = m.model_copy(deep=True)
        clone.set_real_score(gh, ga)
        played.append(clone)

        winner_id = home.team_id if gh > ga else away.team_id if ga > gh else None
        flags = _flags(home, away, gh, ga, winner_id, params)
        summary["group_draws"] += winner_id is None
        summary["fixed_matches" if fixed else "sampled_matches"] += 1
        count_flags(flags)

        entry = {
            "match_id": m.match_id,
            "group": m.group,
            "matchday": m.matchday,
            "home_team": named(m.home_team),
            "away_team": named(m.away_team),
            "score": f"{gh}-{ga}",
            "winner": named(winner_id) if winner_id else None,
            "fixed": fixed,
        }
        if flags:
            entry["flags"] = flags
        group_entries.append(entry)

    # --- Classificação e chaveamento deste cenário -------------------------
    standings = compute_all_standings(played, teams)
    thirds = best_third_placed(standings, teams)
    third_slots = _assign_thirds(thirds)
    winners, runners = standings.winners(), standings.runners_up()
    best_third_ids = {r.team_id for r in thirds}

    standings_out = {
        group: [
            {
                "position": pos,
                "team": named(r.team_id),
                "points": r.points,
                "goal_difference": r.goal_difference,
                "goals_for": r.goals_for,
                "qualifies": pos <= 2 or (pos == 3 and r.team_id in best_third_ids),
            }
            for pos, r in enumerate(table, start=1)
        ]
        for group, table in standings.tables.items()
    }

    def resolve(slot: str) -> str:
        if slot.startswith("1"):
            return winners[slot[1]]
        if slot.startswith("2"):
            return runners[slot[1]]
        return third_slots[slot]

    # --- Eliminatórias: amostra cada jogo; empate -> pênaltis ---------------
    outcomes: dict[int, tuple[str, str]] = {}  # num -> (vencedor, perdedor)

    def ref(r: str) -> str:
        num = int(r[1:])
        return outcomes[num][0] if r[0] == "W" else outcomes[num][1]

    def play(num: int, home_id: str, away_id: str) -> dict:
        home, away = teams[home_id], teams[away_id]
        gh, ga = _sample_score(home, away, params, rng)
        penalties = gh == ga
        if penalties:
            r_home, r_away = _pair_ratings(home, away, params)
            p_home = 1.0 / (1.0 + 10 ** ((r_away - r_home) / params.elo_divisor))
            winner_id = home_id if rng.random() < p_home else away_id
        else:
            winner_id = home_id if gh > ga else away_id
        loser_id = away_id if winner_id == home_id else home_id
        outcomes[num] = (winner_id, loser_id)

        flags = _flags(home, away, gh, ga, winner_id, params)
        summary["sampled_matches"] += 1
        summary["penalty_shootouts"] += penalties
        count_flags(flags)

        entry = {
            "match_id": f"m{num}",
            "home_team": named(home_id),
            "away_team": named(away_id),
            "score": f"{gh}-{ga}",
            "winner": named(winner_id),
            "penalties": penalties,
        }
        if flags:
            entry["flags"] = flags
        return entry

    knockout_out: dict[str, list[dict]] = {
        "round_of_32": [play(num, resolve(hs), resolve(as_)) for num, hs, as_ in ROUND_OF_32]
    }
    for struct, key in (
        (ROUND_OF_16, "round_of_16"),
        (QUARTER_FINALS, "quarter_finals"),
        (SEMI_FINALS, "semi_finals"),
    ):
        knockout_out[key] = [play(num, ref(hr), ref(ar)) for num, hr, ar in struct]
    for (num, hr, ar), key in ((THIRD_PLACE, "third_place"), (FINAL, "final")):
        knockout_out[key] = [play(num, ref(hr), ref(ar))]

    n_group = len(group_entries)
    summary["group_draw_rate_pct"] = (
        round(100 * summary["group_draws"] / n_group, 1) if n_group else 0.0
    )

    return {
        "kind": "sampled_scenario",
        "seed": seed,
        "note": (
            "Cenário AMOSTRADO da distribuição do modelo (1 sorteio de Monte "
            "Carlo), não a previsão mais provável: empates, zebras e goleadas "
            "aparecem aqui na frequência estatística esperada. Jogos de grupo "
            "já disputados estão fixados (fixed=true). Repetir a chamada gera "
            "outro cenário; use `seed` para reproduzir."
        ),
        "summary": summary,
        "champion": named(outcomes[FINAL[0]][0]),
        "group_stage": {"matches": group_entries, "standings": standings_out},
        "knockout": knockout_out,
    }
