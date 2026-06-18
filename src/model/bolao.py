"""Funções objetivo dos bolões e otimizador de palpites por valor esperado.

O `predicted_score` do simulador é a moda da grade de placares — maximiza
P(placar exato). Mas cada bolão pontua acertos PARCIAIS à sua maneira, e o
palpite que maximiza E[pontos] sob a função do bolão raramente coincide com a
moda (ex.: um placar vizinho de muitos placares prováveis vale mais quando
"gols de um time" pontua). Este módulo calcula E[pontos] de forma EXATA sobre
a grade analítica P(i, j) do simulador e devolve o palpite ótimo por jogo.

Bolões suportados (`ruleset`):

  "pragma" — Copa Pragma 2026 (bolaoai.com.br):
      placar exato 2 pts; vencedor correto ou empate sem exato 1 pt.
      Multiplicador por fase: 32-avos 2x, oitavas 4x, quartas 6x, semis 8x,
      3º lugar 8x, final 10x. Bónus por acertar quem AVANÇA no mata-mata
      (pênaltis contam): +2/+3/+5/+7/+10. No mata-mata o palpite tem de ser
      decisivo (o sistema infere o classificado a partir do placar).

  "bcf" — bolão BCF (capturas IMG_1884–1886):
      placar exato 6; vencedor + gols de um time 4; vencedor 3; gols de um
      time sem vencedor 1; empate exato 6; empate sem placar 3. No mata-mata,
      palpite de empate inclui o vencedor dos pênaltis: acertá-lo vale +3
      (9/6/6/3 conforme a tabela); palpite decisivo nunca pontua pênaltis.

Aproximações (assumidas e documentadas):
  - A grade P(i, j) é tratada como a distribuição do "resultado em campo"
    (90' + prorrogação); a prorrogação não é modelada em separado.
  - P(avançar | empate) e P(vencer pênaltis | empate) seguem a convenção do
    projeto: redistribuição proporcional da massa de empate, p_h/(p_h + p_a).
  - O multiplicador de fase não altera o palpite ótimo dentro de um jogo
    (escala constante), mas entra no E[pontos] reportado; o bónus de avanço
    do Pragma NÃO é multiplicado (secção própria do regulamento).
"""

from __future__ import annotations

from .schemas import Phase

RULESETS = ("pragma", "bcf")

# Copa Pragma — multiplicador por fase e bónus por acertar quem avança.
PRAGMA_MULTIPLIER: dict[Phase, int] = {
    Phase.GROUP_STAGE: 1,
    Phase.ROUND_OF_32: 2,
    Phase.ROUND_OF_16: 4,
    Phase.QUARTER_FINALS: 6,
    Phase.SEMI_FINALS: 8,
    Phase.THIRD_PLACE: 8,
    Phase.FINAL: 10,
}
PRAGMA_ADVANCE_BONUS: dict[Phase, int] = {
    Phase.ROUND_OF_32: 2,
    Phase.ROUND_OF_16: 3,
    Phase.QUARTER_FINALS: 5,
    Phase.SEMI_FINALS: 7,
    Phase.FINAL: 10,
}

# Bolão BCF — bónus por acertar o vencedor dos pênaltis (palpite de empate).
BCF_PENALTY_BONUS = 3


def _same_outcome(ph: int, pa: int, ah: int, aa: int) -> bool:
    return (ph > pa) == (ah > aa) and (ph < pa) == (ah < aa)


def pragma_base_points(ph: int, pa: int, ah: int, aa: int) -> int:
    """Pontos do palpite (ph, pa) com resultado (ah, aa) — sem multiplicador."""
    if (ph, pa) == (ah, aa):
        return 2
    if _same_outcome(ph, pa, ah, aa):
        return 1
    return 0


def bcf_base_points(ph: int, pa: int, ah: int, aa: int) -> int:
    """Pontos do palpite (ph, pa) com resultado (ah, aa) — sem pênaltis."""
    if (ph, pa) == (ah, aa):
        return 6
    side = ph == ah or pa == aa  # acertou os gols de um dos times
    if _same_outcome(ph, pa, ah, aa):
        if ah == aa:
            return 3  # empate sem placar (gols de um lado baterem => seria exato)
        return 4 if side else 3
    return 1 if side else 0


def _draw_split(matrix: list[list[float]]) -> tuple[float, float, float]:
    """(p_home, p_draw, p_away) da grade."""
    p_home = p_draw = p_away = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
    return p_home, p_draw, p_away


def tiebreak_probability(matrix: list[list[float]]) -> float:
    """P(casa prevalece | empate em campo) — redistribuição proporcional.

    Convenção do projeto (mesma de `predict_match(knockout=True)`); usada
    tanto para 'quem avança' como para 'quem vence os pênaltis'.
    """
    p_home, _, p_away = _draw_split(matrix)
    decisive = p_home + p_away
    return p_home / decisive if decisive > 0 else 0.5


def expected_points_pragma(
    matrix: list[list[float]],
    pred: tuple[int, int],
    phase: Phase,
) -> float:
    """E[pontos] do palpite no Pragma: base x multiplicador + bónus de avanço."""
    ph, pa = pred
    base = sum(
        p * pragma_base_points(ph, pa, i, j)
        for i, row in enumerate(matrix)
        for j, p in enumerate(row)
    )
    ev = base * PRAGMA_MULTIPLIER[phase]
    bonus = PRAGMA_ADVANCE_BONUS.get(phase, 0)
    if bonus and ph != pa:
        p_home, p_draw, p_away = _draw_split(matrix)
        p_tb = tiebreak_probability(matrix)
        p_adv_home = p_home + p_draw * p_tb
        ev += bonus * (p_adv_home if ph > pa else 1.0 - p_adv_home)
    return ev


def expected_points_bcf(
    matrix: list[list[float]],
    pred: tuple[int, int],
    knockout: bool,
    penalty_pick_home: bool | None = None,
) -> float:
    """E[pontos] do palpite no bolão BCF.

    No mata-mata, um palpite de empate exige `penalty_pick_home` (quem vence
    os pênaltis); palpites decisivos nunca pontuam pênaltis (regra do BCF).
    """
    ph, pa = pred
    ev = sum(
        p * bcf_base_points(ph, pa, i, j)
        for i, row in enumerate(matrix)
        for j, p in enumerate(row)
    )
    if knockout and ph == pa:
        if penalty_pick_home is None:
            raise ValueError("Palpite de empate no mata-mata exige o vencedor dos pênaltis.")
        p_tb = tiebreak_probability(matrix)
        p_correct = p_tb if penalty_pick_home else 1.0 - p_tb
        _, p_draw, _ = _draw_split(matrix)
        ev += BCF_PENALTY_BONUS * p_correct * p_draw
    return ev


def best_picks(
    matrix: list[list[float]],
    ruleset: str,
    phase: Phase,
    top: int = 3,
) -> list[dict]:
    """Palpites ordenados por E[pontos] decrescente (os `top` melhores).

    Cada item: {"score": (h, a), "expected_points": float} e, quando aplicável,
    "penalty_winner_home": bool (bcf, empate no mata-mata) ou
    "advancer_home": bool (pragma, mata-mata).
    """
    if ruleset not in RULESETS:
        raise ValueError(f"Bolão desconhecido: '{ruleset}'. Válidos: {RULESETS}")
    knockout = phase.is_knockout
    n = len(matrix)
    candidates: list[dict] = []
    for i in range(n):
        for j in range(n):
            if ruleset == "pragma":
                if knockout and i == j:
                    # O sistema infere o classificado a partir do placar:
                    # palpite de empate é ambíguo no mata-mata.
                    continue
                ev = expected_points_pragma(matrix, (i, j), phase)
                item = {"score": (i, j), "expected_points": ev}
                if knockout:
                    item["advancer_home"] = i > j
            else:
                if knockout and i == j:
                    p_tb = tiebreak_probability(matrix)
                    pens_home = p_tb >= 0.5
                    ev = expected_points_bcf(matrix, (i, j), knockout, pens_home)
                    item = {
                        "score": (i, j),
                        "expected_points": ev,
                        "penalty_winner_home": pens_home,
                    }
                else:
                    ev = expected_points_bcf(matrix, (i, j), knockout)
                    item = {"score": (i, j), "expected_points": ev}
            candidates.append(item)
    candidates.sort(key=lambda c: (-c["expected_points"], sum(c["score"]), c["score"]))
    return candidates[:top]


def evaluate_pick(
    matrix: list[list[float]],
    pred: tuple[int, int],
    ruleset: str,
    phase: Phase,
) -> float:
    """E[pontos] de um palpite arbitrário (ex.: o placar modal, p/ comparação)."""
    if ruleset == "pragma":
        if phase.is_knockout and pred[0] == pred[1]:
            return 0.0  # palpite inválido no mata-mata do Pragma
        return expected_points_pragma(matrix, pred, phase)
    if phase.is_knockout and pred[0] == pred[1]:
        p_tb = tiebreak_probability(matrix)
        return expected_points_bcf(matrix, pred, True, p_tb >= 0.5)
    return expected_points_bcf(matrix, pred, phase.is_knockout)
