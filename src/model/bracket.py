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

from ..data.calendar import KNOCKOUT_KICKOFFS
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
# Lnn = perdedor (usado só na disputa de 3º lugar). Combinações OFICIAIS FIFA.
ROUND_OF_16 = [(89, "W74", "W77"), (90, "W73", "W75"), (91, "W76", "W78"),
               (92, "W79", "W80"), (93, "W83", "W84"), (94, "W81", "W82"),
               (95, "W86", "W88"), (96, "W85", "W87")]
QUARTER_FINALS = [(97, "W89", "W90"), (98, "W93", "W94"),
                  (99, "W91", "W92"), (100, "W95", "W96")]
SEMI_FINALS = [(101, "W97", "W98"), (102, "W99", "W100")]
THIRD_PLACE = (103, "L101", "L102")
FINAL = (104, "W101", "W102")


# ---------------------------------------------------------------------------
# Atribuição dos 8 terceiros aos 8 slots de terceiros (emparelhamento legal).
# ---------------------------------------------------------------------------
def _third_slot_groups(slot: str) -> set[str]:
    """'3A/B/C/D/F' -> {'A','B','C','D','F'}."""
    return set(slot[1:].split("/"))


# Tabela OFICIAL FIFA de atribuição dos 8 terceiros aos slots de terceiros,
# por combinação de grupos qualificados (chave = frozenset dos 8 grupos ->
# {slot: grupo}). O emparelhamento por backtracking encontra UMA atribuição
# legal, mas a FIFA usa uma tabela fixa; preenche-se aqui a combinação já
# realizada (Copa 2026) para reproduzir o chaveamento REAL. Combinações não
# tabeladas recorrem ao backtracking.
OFFICIAL_THIRD_ASSIGNMENT: dict[frozenset, dict[str, str]] = {
    # 2026: terceiros qualificados vêm dos grupos B,D,E,F,I,J,K,L.
    frozenset("BDEFIJKL"): {
        "3A/B/C/D/F": "D", "3C/D/F/G/H": "F", "3C/E/F/H/I": "E",
        "3E/H/I/J/K": "K", "3B/E/F/I/J": "B", "3A/E/H/I/J": "I",
        "3E/F/G/I/J": "J", "3D/E/I/J/L": "L",
    },
}


def assign_third_slots(
    third_records: list[TeamRecord],
    use_official: bool = True,
) -> dict[str, str]:
    """Mapeia cada slot de terceiros -> team_id, respeitando os grupos permitidos.

    Resolve por backtracking um emparelhamento perfeito entre os 8 terceiros
    qualificados e os 8 slots; é determinístico (slots e candidatos em ordem
    fixa) e devolve uma atribuição legal do chaveamento.

    `use_official=True` (default, usado no chaveamento REAL) prefere a tabela
    oficial FIFA quando a combinação de grupos está tabelada. O Monte Carlo de
    cenários passa `use_official=False` para manter o emparelhamento genérico
    em torneios hipotéticos (não enviesar as simulações por uma única combinação
    tabelada).
    """
    slots = [away for _, _, away in ROUND_OF_32 if away.startswith("3")]
    allowed = {slot: _third_slot_groups(slot) for slot in slots}
    thirds_by_group = {r.group: r.team_id for r in third_records}
    qualified_groups = list(thirds_by_group.keys())

    # Atribuição oficial FIFA quando a combinação de grupos está tabelada.
    if use_official:
        official = OFFICIAL_THIRD_ASSIGNMENT.get(frozenset(thirds_by_group))
        if official:
            return {slot: thirds_by_group[grp] for slot, grp in official.items()}

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
                kickoff_utc=KNOCKOUT_KICKOFFS.get(num),
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


def _winner_loser(
    match: Match, penalty_winners: dict[str, str] | None = None
) -> tuple[str, str]:
    """(vencedor, perdedor) de um jogo eliminatório.

    Usa o resultado REAL quando disponível; em empate no tempo normal (decidido
    nos pênaltis), usa o vencedor dos pênaltis informado (`penalty_winners`) e,
    em falta dele, recorre ao favorito do modelo como aproximação. Caso não haja
    resultado real, usa o vencedor previsto.
    """
    other = lambda w: match.away_team if w == match.home_team else match.home_team
    if match.real_score is not None:
        s = match.real_score
        if s.home_goals > s.away_goals:
            return match.home_team, match.away_team
        if s.away_goals > s.home_goals:
            return match.away_team, match.home_team
        # Empate -> pênaltis: usa o vencedor informado; senão, o favorito.
        w = (penalty_winners or {}).get(match.match_id) or match.prediction.expected_winner
        return w, other(w)
    w = match.prediction.expected_winner
    return w, other(w)


def simulate_knockouts(
    round_of_32: list[Match],
    teams: dict[str, Team],
    params: ModelParams = DEFAULT_PARAMS,
    real_results: dict[str, tuple[int, int]] | None = None,
    penalty_winners: dict[str, str] | None = None,
) -> dict[Phase, list[Match]]:
    """Prevê (ou aplica resultados reais de) todas as fases eliminatórias.

    `real_results` mapeia match_id ("m73"...) -> (gols_casa, gols_fora). Para os
    jogos já disputados usa-se o placar REAL para decidir quem avança; os restantes
    são previstos em modo `knockout=True`. Assim os resultados reais PROPAGAM-se
    pelas fases seguintes (quem realmente venceu segue para a ronda seguinte).
    `penalty_winners` (match_id -> team_id) resolve quem avança quando o jogo real
    terminou empatado e foi decidido nos pênaltis. A previsão é sempre calculada
    também, para exibir confiança/probabilidades.
    """
    real_results = real_results or {}
    penalty_winners = penalty_winners or {}

    def fill(matches: list[Match]) -> None:
        for m in matches:
            m.prediction = predict_match(
                teams[m.home_team], teams[m.away_team], params, knockout=True
            )
            if m.match_id in real_results:
                hg, ag = real_results[m.match_id]
                m.set_real_score(hg, ag)
            m._sync_status()

    fill(round_of_32)
    rounds: dict[Phase, list[Match]] = {Phase.ROUND_OF_32: round_of_32}

    # Resultados por jogo: número -> (vencedor, perdedor).
    outcomes: dict[int, tuple[str, str]] = {}
    for m in round_of_32:
        outcomes[int(m.match_id[1:])] = _winner_loser(m, penalty_winners)

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
                kickoff_utc=KNOCKOUT_KICKOFFS.get(num),
            )
            for num, home_ref, away_ref in _PHASE_OF[phase]
        ]
        fill(matches)
        for m in matches:
            outcomes[int(m.match_id[1:])] = _winner_loser(m, penalty_winners)
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
            kickoff_utc=KNOCKOUT_KICKOFFS.get(num),
        )
        fill([m])
        outcomes[num] = _winner_loser(m, penalty_winners)
        rounds[phase] = [m]

    return rounds
