"""Serialização de partidas/previsões para dicts simples (consumo via MCP/JSON)."""

from __future__ import annotations

from ..model.schemas import Match, Phase, Team


def match_to_dict(match: Match, teams: dict[str, Team]) -> dict:
    """Converte uma partida (com previsão e/ou resultado real) num dict limpo."""
    def name(tid: str) -> str:
        return teams[tid].name if tid in teams else tid

    out: dict = {
        "match_id": match.match_id,
        "phase": match.phase.value,
        "group": match.group,
        "home_team": {"id": match.home_team, "name": name(match.home_team)},
        "away_team": {"id": match.away_team, "name": name(match.away_team)},
        "status": match.status.value,
    }
    # Rodada da fase de grupos (1–3); ausente nas eliminatórias.
    if match.matchday is not None:
        out["matchday"] = match.matchday

    if match.prediction is not None:
        p = match.prediction
        winner_id = p.expected_winner
        out["prediction"] = {
            "predicted_score": f"{p.predicted_score.home_goals}-{p.predicted_score.away_goals}",
            "expected_winner": (
                {"id": winner_id, "name": name(winner_id)} if winner_id else None
            ),
            "confidence_level": p.confidence_level,
            "probabilities": {
                "home": round(p.prob_home * 100, 1),
                "draw": round(p.prob_draw * 100, 1),
                "away": round(p.prob_away * 100, 1),
            },
        }
        # Alerta de equilíbrio: o vencedor previsto é só a moda dos desfechos;
        # aqui sinalizamos quando ele não é um favorito de verdade. Apenas na
        # fase de grupos, onde o empate é um desfecho final possível.
        if match.phase is Phase.GROUP_STAGE and p.is_balanced:
            out["prediction"]["note"] = (
                f"equilibrado — sem favorito claro; empate com "
                f"{round(p.prob_draw * 100)}% de probabilidade"
            )
            out.setdefault("flags", []).append("balanced")

    if match.real_score is not None:
        out["real_score"] = (
            f"{match.real_score.home_goals}-{match.real_score.away_goals}"
        )

    # Marca jogos com vaga de playoff ainda por definir.
    if teams.get(match.home_team) and teams[match.home_team].is_placeholder:
        out.setdefault("flags", []).append("home_tbd")
    if teams.get(match.away_team) and teams[match.away_team].is_placeholder:
        out.setdefault("flags", []).append("away_tbd")

    return out
