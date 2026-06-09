"""Passo 3 (parte C) — Orquestração do torneio completo.

Liga o pipeline ponta a ponta: prevê a fase de grupos, calcula a classificação,
seleciona os 8 melhores terceiros, monta os 32-avos oficiais e propaga os
vencedores até à final. Este módulo é a base para a ferramenta MCP
`get_phase_predictions(phase_name)`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..data.ratings import build_group_stage_matches, build_teams
from .bracket import build_round_of_32, simulate_knockouts
from .schemas import Match, Phase, Team
from .simulator import DEFAULT_PARAMS, ModelParams, predict_match
from .standings import GroupStandings, best_third_placed, compute_all_standings


@dataclass
class TournamentResult:
    """Estado completo do torneio previsto, indexado por fase."""

    teams: dict[str, Team]
    group_matches: list[Match]
    standings: GroupStandings
    rounds: dict[Phase, list[Match]] = field(default_factory=dict)

    def phase(self, phase: Phase) -> list[Match]:
        if phase is Phase.GROUP_STAGE:
            return self.group_matches
        return self.rounds.get(phase, [])

    @property
    def champion(self) -> str | None:
        final = self.rounds.get(Phase.FINAL)
        return final[0].prediction.expected_winner if final else None


def predict_group_stage(
    teams: dict[str, Team],
    params: ModelParams = DEFAULT_PARAMS,
) -> list[Match]:
    """Prevê os 72 jogos da fase de grupos."""
    matches = build_group_stage_matches()
    for m in matches:
        m.prediction = predict_match(teams[m.home_team], teams[m.away_team], params)
        m._sync_status()
    return matches


def run_full_tournament(
    teams: dict[str, Team] | None = None,
    params: ModelParams = DEFAULT_PARAMS,
) -> TournamentResult:
    """Executa o pipeline completo e devolve o estado por fase."""
    teams = teams or build_teams()
    group_matches = predict_group_stage(teams, params)
    standings = compute_all_standings(group_matches, teams)
    r32 = build_round_of_32(standings, teams)
    rounds = simulate_knockouts(r32, teams, params)
    return TournamentResult(
        teams=teams,
        group_matches=group_matches,
        standings=standings,
        rounds=rounds,
    )


# ---------------------------------------------------------------------------
# Demo executável: python -m src.model.tournament
# ---------------------------------------------------------------------------
def _name(result: TournamentResult, tid: str) -> str:  # pragma: no cover
    return result.teams[tid].name


def _print_standings(result: TournamentResult) -> None:  # pragma: no cover
    print("\n=== CLASSIFICAÇÃO PREVISTA DOS GRUPOS ===")
    for group, table in result.standings.tables.items():
        print(f"\nGrupo {group}:")
        print(f"  {'#':<2}{'Seleção':<26}{'P':>3}{'J':>3}{'SG':>4}{'GP':>4}")
        for pos, r in enumerate(table, start=1):
            mark = "✓" if pos <= 2 else ("·" if pos == 3 else " ")
            print(
                f" {mark}{pos:<2}{_name(result, r.team_id):<26}"
                f"{r.points:>3}{r.played:>3}{r.goal_difference:>+4}{r.goals_for:>4}"
            )


def _print_thirds(result: TournamentResult) -> None:  # pragma: no cover
    thirds = best_third_placed(result.standings, result.teams)
    print("\n=== 8 MELHORES TERCEIROS (qualificados) ===")
    for r in thirds:
        print(f"  Grupo {r.group}: {_name(result, r.team_id):<24} "
              f"{r.points} pts, SG {r.goal_difference:+d}")


def _print_knockouts(result: TournamentResult) -> None:  # pragma: no cover
    labels = {
        Phase.ROUND_OF_32: "32-AVOS DE FINAL",
        Phase.ROUND_OF_16: "OITAVAS DE FINAL",
        Phase.QUARTER_FINALS: "QUARTAS DE FINAL",
        Phase.SEMI_FINALS: "SEMIFINAIS",
        Phase.THIRD_PLACE: "DISPUTA DO 3º LUGAR",
        Phase.FINAL: "FINAL",
    }
    for phase, label in labels.items():
        matches = result.phase(phase)
        if not matches:
            continue
        print(f"\n=== {label} ===")
        for m in matches:
            p = m.prediction
            confronto = f"{_name(result, m.home_team)} x {_name(result, m.away_team)}"
            winner = _name(result, p.expected_winner)
            print(f"  {confronto:<42} {str(p.predicted_score):<6} "
                  f"-> {winner:<16} ({p.confidence_level:.1f}%)")


def _demo() -> None:  # pragma: no cover
    result = run_full_tournament()
    _print_standings(result)
    _print_thirds(result)
    _print_knockouts(result)
    print(f"\n🏆 CAMPEÃO PREVISTO: {_name(result, result.champion)}\n")


if __name__ == "__main__":  # pragma: no cover
    _demo()
