"""Calendário OFICIAL da Copa 2026 — confrontos por rodada e kickoffs em UTC.

Fonte: tabela oficial FIFA (divulgada após o sorteio de dez/2025), conferida
em duas fontes independentes (horários CEST e BST convertidos para UTC).

Substitui o round-robin sintético: os pareamentos por rodada e o mando de
campo aqui são os REAIS — essencial para inserir resultados por match_id e
para filtrar jogos por data (`get_matches_by_date`).
"""

from __future__ import annotations

from datetime import datetime, timezone

# Fixtures oficiais por grupo: (matchday, casa, fora, kickoff UTC).
# Dentro de cada (grupo, rodada), a ordem é cronológica (define o slot do
# match_id: ex. "B11" = 1º jogo da rodada 1 do grupo B).
GROUP_FIXTURES: dict[str, list[tuple[int, str, str, str]]] = {
    "A": [
        (1, "MEX", "RSA", "2026-06-11T19:00Z"),
        (1, "KOR", "CZE", "2026-06-12T02:00Z"),
        (2, "CZE", "RSA", "2026-06-18T16:00Z"),
        (2, "MEX", "KOR", "2026-06-19T01:00Z"),
        (3, "RSA", "KOR", "2026-06-25T01:00Z"),
        (3, "CZE", "MEX", "2026-06-25T01:00Z"),
    ],
    "B": [
        (1, "CAN", "BIH", "2026-06-12T19:00Z"),
        (1, "QAT", "SUI", "2026-06-13T19:00Z"),
        (2, "SUI", "BIH", "2026-06-18T19:00Z"),
        (2, "CAN", "QAT", "2026-06-18T22:00Z"),
        (3, "SUI", "CAN", "2026-06-24T19:00Z"),
        (3, "BIH", "QAT", "2026-06-24T19:00Z"),
    ],
    "C": [
        (1, "BRA", "MAR", "2026-06-13T22:00Z"),
        (1, "HAI", "SCO", "2026-06-14T01:00Z"),
        (2, "SCO", "MAR", "2026-06-19T22:00Z"),
        (2, "BRA", "HAI", "2026-06-20T00:30Z"),
        (3, "MAR", "HAI", "2026-06-24T22:00Z"),
        (3, "SCO", "BRA", "2026-06-24T22:00Z"),
    ],
    "D": [
        (1, "USA", "PAR", "2026-06-13T01:00Z"),
        (1, "AUS", "TUR", "2026-06-14T04:00Z"),
        (2, "USA", "AUS", "2026-06-19T19:00Z"),
        (2, "TUR", "PAR", "2026-06-20T03:00Z"),
        (3, "TUR", "USA", "2026-06-26T02:00Z"),
        (3, "PAR", "AUS", "2026-06-26T02:00Z"),
    ],
    "E": [
        (1, "GER", "CUW", "2026-06-14T17:00Z"),
        (1, "CIV", "ECU", "2026-06-14T23:00Z"),
        (2, "GER", "CIV", "2026-06-20T20:00Z"),
        (2, "ECU", "CUW", "2026-06-21T00:00Z"),
        (3, "CUW", "CIV", "2026-06-25T20:00Z"),
        (3, "ECU", "GER", "2026-06-25T20:00Z"),
    ],
    "F": [
        (1, "NED", "JPN", "2026-06-14T20:00Z"),
        (1, "SWE", "TUN", "2026-06-15T02:00Z"),
        (2, "NED", "SWE", "2026-06-20T17:00Z"),
        (2, "TUN", "JPN", "2026-06-21T04:00Z"),
        (3, "TUN", "NED", "2026-06-25T23:00Z"),
        (3, "JPN", "SWE", "2026-06-25T23:00Z"),
    ],
    "G": [
        (1, "BEL", "EGY", "2026-06-15T19:00Z"),
        (1, "IRN", "NZL", "2026-06-16T01:00Z"),
        (2, "BEL", "IRN", "2026-06-21T19:00Z"),
        (2, "NZL", "EGY", "2026-06-22T01:00Z"),
        (3, "NZL", "BEL", "2026-06-27T03:00Z"),
        (3, "EGY", "IRN", "2026-06-27T03:00Z"),
    ],
    "H": [
        (1, "ESP", "CPV", "2026-06-15T16:00Z"),
        (1, "KSA", "URU", "2026-06-15T22:00Z"),
        (2, "ESP", "KSA", "2026-06-21T16:00Z"),
        (2, "URU", "CPV", "2026-06-21T22:00Z"),
        (3, "CPV", "KSA", "2026-06-27T00:00Z"),
        (3, "URU", "ESP", "2026-06-27T00:00Z"),
    ],
    "I": [
        (1, "FRA", "SEN", "2026-06-16T19:00Z"),
        (1, "IRQ", "NOR", "2026-06-16T22:00Z"),
        (2, "FRA", "IRQ", "2026-06-22T21:00Z"),
        (2, "NOR", "SEN", "2026-06-23T00:00Z"),
        (3, "NOR", "FRA", "2026-06-26T19:00Z"),
        (3, "SEN", "IRQ", "2026-06-26T19:00Z"),
    ],
    "J": [
        (1, "ARG", "ALG", "2026-06-17T01:00Z"),
        (1, "AUT", "JOR", "2026-06-17T04:00Z"),
        (2, "ARG", "AUT", "2026-06-22T17:00Z"),
        (2, "JOR", "ALG", "2026-06-23T03:00Z"),
        (3, "ALG", "AUT", "2026-06-28T02:00Z"),
        (3, "JOR", "ARG", "2026-06-28T02:00Z"),
    ],
    "K": [
        (1, "POR", "COD", "2026-06-17T17:00Z"),
        (1, "UZB", "COL", "2026-06-18T02:00Z"),
        (2, "POR", "UZB", "2026-06-23T17:00Z"),
        (2, "COL", "COD", "2026-06-24T02:00Z"),
        (3, "COL", "POR", "2026-06-27T23:30Z"),
        (3, "COD", "UZB", "2026-06-27T23:30Z"),
    ],
    "L": [
        (1, "ENG", "CRO", "2026-06-17T20:00Z"),
        (1, "GHA", "PAN", "2026-06-17T23:00Z"),
        (2, "ENG", "GHA", "2026-06-23T20:00Z"),
        (2, "PAN", "CRO", "2026-06-23T23:00Z"),
        (3, "PAN", "ENG", "2026-06-27T21:00Z"),
        (3, "CRO", "GHA", "2026-06-27T21:00Z"),
    ],
}

# Kickoffs oficiais das eliminatórias, por número de jogo FIFA (m73–m104).
KNOCKOUT_KICKOFFS: dict[int, str] = {
    # 32-avos (28/06 – 03/07)
    73: "2026-06-28T19:00Z", 74: "2026-06-29T20:30Z", 75: "2026-06-30T01:00Z",
    76: "2026-06-29T17:00Z", 77: "2026-06-30T21:00Z", 78: "2026-06-30T17:00Z",
    79: "2026-07-01T01:00Z", 80: "2026-07-01T16:00Z", 81: "2026-07-02T00:00Z",
    82: "2026-07-01T20:00Z", 83: "2026-07-02T23:00Z", 84: "2026-07-02T19:00Z",
    85: "2026-07-03T03:00Z", 86: "2026-07-03T22:00Z", 87: "2026-07-04T01:30Z",
    88: "2026-07-03T18:00Z",
    # Oitavas (04/07 – 07/07)
    89: "2026-07-04T21:00Z", 90: "2026-07-04T17:00Z", 91: "2026-07-05T20:00Z",
    92: "2026-07-06T00:00Z", 93: "2026-07-06T19:00Z", 94: "2026-07-07T00:00Z",
    95: "2026-07-07T16:00Z", 96: "2026-07-07T20:00Z",
    # Quartas (09/07 – 12/07)
    97: "2026-07-09T20:00Z", 98: "2026-07-10T19:00Z",
    99: "2026-07-11T21:00Z", 100: "2026-07-12T01:00Z",
    # Semis, 3º lugar e final
    101: "2026-07-14T19:00Z", 102: "2026-07-15T19:00Z",
    103: "2026-07-18T21:00Z", 104: "2026-07-19T19:00Z",
}


def parse_kickoff(value: str) -> datetime:
    """ISO-8601 com sufixo 'Z' -> datetime timezone-aware (compatível 3.10)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )
