"""Passo 3 (parte B) — Montagem e progressão do chaveamento eliminatório.

Reconstrói o chaveamento OFICIAL da Copa 2026 (32-avos → final) a partir da
classificação dos grupos, resolvendo:
  • slots fixos: "1A" (1º do grupo A), "2B" (2º do grupo B), ...
  • slots de terceiros: "3A/B/C/D/F" (um terceiro qualificado de um daqueles
    grupos), atribuídos por emparelhamento que respeita os grupos permitidos
    em cada confronto (a tabela de combinações oficial da FIFA pode ser
    inserida aqui depois para reproduzir a atribuição exacta).

Depois propaga os vencedores ronda a ronda (W73 = vencedor do jogo 73, etc.),
usando o simulador em modo eliminatório (sem empate).
"""

from __future__ import annotations

from .schemas import Match, Phase, Team
from .simulator import DEFAULT_PARAMS, ModelParams, predict_match
from .standings import GroupStandings, TeamRecord, best_third_placed

# ---------------------------------------------------------------------------
# Estrutura oficial dos jogos (match_number -> (slot_casa, slot_fora, fase)).
# Slots de terceiros guardam o conjunto de grupos permitidos.
# ---------------------------------------------------------------------------
ROUND_OF_32: list[tuple[int, str, str]] = [
    (73, "2A", "2B"),
    (74, "1E", "3A/B/C/D/F"),
    (75, "1F", "2C"),
    (76, "1C", "2F"),
    (77, "1I", "3C/D/F/G/H"),
    (78, "2E", "2I"),
    (79, "1A", "3C/E/F/H/I"),
    (80, "1L", "3E/H/I/J/K"),
    (81, "1D", "3B/E/F/I/J"),
    (82, "1G", "3A/E/H/I/J"),
    (83, "2K", "2L"),
    (84, "1H", "2J"),
    (85, "1B", "3E/F/G/I/J"),
    (86, "1J", "2H"),
    (87, "1K", "3D/E/I/J/L"),
    (88, "2D", "2G"),
]

# Fases seguintes: (match_number, ref_casa, ref_fora). Wnn = vencedor do jogo nn;
# Lnn = perdedor (usado só na disputa de 3º lugar).
ROUND_OF_16 = [(89, "W73", "W74"), (90, "W75", "W76"), (91, "W77", "W78"),
               (92, "W79", "W80"), (93, "W81", "W82"), (94, "W83", "W84"),
               (95, "W85", "W86"), (96, "W87", "W88")]
QUARTER_FINALS = [(97, "W89", "W90"), (98, "W91", "W92"),
                  (99, "W93", "W94"), (100, "W95", "W96")]
SEMI_FINALS = [(101, "W97", "W98"), (102, "W99", "W100")]
THIRD_PLACE = (103, "L101", "L102")
FINAL = (104, "W101", "W102")


# ---------------------------------------------------------------------------
# Atribuição dos 8 terceiros aos 8 slots de terceiros (emparelhamento legal).
# ---------------------------------------------------------------------------
def _third_slot_groups(slot: str) -> set[str]:
    """'3A/B/C/D/F' -> {'A','B','C','D','F'}."""
    return set(slot[1:].split("/"))


def assign_third_slots(
    third_records: list[TeamRecord],
) -> dict[str, str]:
    """Mapeia cada slot de terceiros -> team_id, respeitando os grupos permitidos.

    Resolve por backtracking um emparelhamento perfeito entre os 8 terceiros
    qualificados e os 8 slots; é determinístico (slots e candidatos em ordem
    fixa) e devolve uma atribuição legal do chaveamento.
    """
    slots = [away for _, _, away in ROUND_OF_32 if away.startswith("3")]
    allowed = {slot: _third_slot_groups(slot) for slot in slots}
    thirds_by_group = {r.group: r.team_id for r in third_records}
    qualified_groups = list(thirds_by_group.keys())

    assignment: dict[str, str] = {}
    used: set[str] = set()

    def backtrack(idx: int) -> bool:
        if idx == len(slots):
            return True
        slot = slots[idx]
        for group in qualified_groups:
            if group not in used and group in allowed[slot]:
                used.add(group)
                assignment[slot] = thirds_by_group[group]
                if backtrack(idx + 1):
                    return True
                used.remove(group)
                del assignment[slot]
        return False

    if not backtrack(0):
        raise ValueError(
            "Não foi possível atribuir os terceiros aos slots (combinação inválida)."
        )
    return assignment


# ---------------------------------------------------------------------------
# Construção dos 32-avos a partir da classificação.
# ---------------------------------------------------------------------------
def build_round_of_32(
    standings: GroupStandings,
    teams: dict[str, Team],
) -> list[Match]:
    """Resolve os 16 confrontos dos 32-avos com seleções concretas."""
    thirds = best_third_placed(standings, teams)
    third_slots = assign_third_slots(thirds)
    winners = standings.winners()
    runners = standings.runners_up()

    def resolve(slot: str) -> str:
        if slot.startswith("1"):
            return winners[slot[1]]
        if slot.startswith("2"):
            return runners[slot[1]]
        if slot.startswith("3"):
            return third_slots[slot]
        raise ValueError(f"Slot desconhecido: {slot}")

    matches: list[Match] = []
    for num, home_slot, away_slot in ROUND_OF_32:
        matches.append(
            Match(
                match_id=f"m{num}",
                phase=Phase.ROUND_OF_32,
                home_team=resolve(home_slot),
                away_team=resolve(away_slot),
            )
        )
    return matches


# ---------------------------------------------------------------------------
# Progressão: prevê cada jogo e avança o vencedor.
# ---------------------------------------------------------------------------
_PHASE_OF = {
    Phase.ROUND_OF_16: ROUND_OF_16,
    Phase.QUARTER_FINALS: QUARTER_FINALS,
    Phase.SEMI_FINALS: SEMI_FINALS,
}


def _winner_loser(match: Match) -> tuple[str, str]:
    """(vencedor, perdedor) de um jogo eliminatório já previsto."""
    w = match.prediction.expected_winner
    loser = match.away_team if w == match.home_team else match.home_team
    return w, loser


def simulate_knockouts(
    round_of_32: list[Match],
    teams: dict[str, Team],
    params: ModelParams = DEFAULT_PARAMS,
) -> dict[Phase, list[Match]]:
    """Prevê todas as fases eliminatórias e devolve os jogos por fase.

    Cada jogo é previsto em modo `knockout=True` (sem empate); o vencedor
    previsto avança. Inclui a disputa do 3º lugar e a final.
    """
    def predict_all(matches: list[Match]) -> None:
        for m in matches:
            m.prediction = predict_match(
                teams[m.home_team], teams[m.away_team], params, knockout=True
            )
            m._sync_status()

    predict_all(round_of_32)
    rounds: dict[Phase, list[Match]] = {Phase.ROUND_OF_32: round_of_32}

    # Resultados por jogo: número -> (vencedor, perdedor).
    outcomes: dict[int, tuple[str, str]] = {}
    for m in round_of_32:
        outcomes[int(m.match_id[1:])] = _winner_loser(m)

    def resolve_ref(ref: str) -> str:
        num = int(ref[1:])
        return outcomes[num][0] if ref[0] == "W" else outcomes[num][1]

    for phase in (Phase.ROUND_OF_16, Phase.QUARTER_FINALS, Phase.SEMI_FINALS):
        matches = [
            Match(
                match_id=f"m{num}",
                phase=phase,
                home_team=resolve_ref(home_ref),
                away_team=resolve_ref(away_ref),
            )
            for num, home_ref, away_ref in _PHASE_OF[phase]
        ]
        predict_all(matches)
        for m in matches:
            outcomes[int(m.match_id[1:])] = _winner_loser(m)
        rounds[phase] = matches

    # Disputa de 3º lugar e final.
    for phase, (num, home_ref, away_ref) in (
        (Phase.THIRD_PLACE, THIRD_PLACE),
        (Phase.FINAL, FINAL),
    ):
        m = Match(
            match_id=f"m{num}",
            phase=phase,
            home_team=resolve_ref(home_ref),
            away_team=resolve_ref(away_ref),
        )
        m.prediction = predict_match(
            teams[m.home_team], teams[m.away_team], params, knockout=True
        )
        m._sync_status()
        outcomes[num] = _winner_loser(m)
        rounds[phase] = [m]

    return rounds
