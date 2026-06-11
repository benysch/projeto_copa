"""Testes do calendário oficial (kickoffs UTC) e do filtro por data."""

from __future__ import annotations

from datetime import date

from src.data.calendar import GROUP_FIXTURES, KNOCKOUT_KICKOFFS, parse_kickoff
from src.data.ratings import build_group_stage_matches, build_teams
from src.service.engine import PredictionEngine


def test_calendar_covers_all_72_group_matches():
    fixtures = [f for rows in GROUP_FIXTURES.values() for f in rows]
    assert len(fixtures) == 72
    # Round-robin íntegro: cada grupo tem os 6 confrontos, sem repetição.
    for group, rows in GROUP_FIXTURES.items():
        pairs = {frozenset((h, a)) for _, h, a, _ in rows}
        assert len(pairs) == 6, f"grupo {group} com confronto repetido/faltando"
    # Todos os kickoffs são ISO-UTC parseáveis e dentro da fase de grupos.
    for _, _, _, kickoff in fixtures:
        d = parse_kickoff(kickoff).date()
        assert date(2026, 6, 11) <= d <= date(2026, 6, 28)


def test_knockout_kickoffs_cover_m73_to_m104():
    assert set(KNOCKOUT_KICKOFFS) == set(range(73, 105))
    final = parse_kickoff(KNOCKOUT_KICKOFFS[104])
    assert final.date() == date(2026, 7, 19)


def test_group_matches_carry_official_kickoff_and_pairings():
    matches = {m.match_id: m for m in build_group_stage_matches()}
    assert len(matches) == 72
    assert all(m.kickoff_utc for m in matches.values())
    # Abertura oficial: México x África do Sul em 11/06 no Azteca.
    opener = matches["A11"]
    assert (opener.home_team, opener.away_team) == ("MEX", "RSA")
    assert parse_kickoff(opener.kickoff_utc).date() == date(2026, 6, 11)
    # Sorteio oficial: rodada 1 do grupo B é CAN x BIH e QAT x SUI.
    assert (matches["B11"].home_team, matches["B11"].away_team) == ("CAN", "BIH")
    assert (matches["B12"].home_team, matches["B12"].away_team) == ("QAT", "SUI")
    # Cada seleção joga exatamente 3 vezes.
    teams = build_teams()
    appearances = {tid: 0 for tid in teams}
    for m in matches.values():
        appearances[m.home_team] += 1
        appearances[m.away_team] += 1
    assert all(n == 3 for n in appearances.values())


def test_matches_between_filters_and_sorts():
    eng = PredictionEngine()
    window = eng.matches_between(date(2026, 6, 11), days=5)
    # 11–15/06: da abertura até Bélgica x Egito / Arábia Saudita x Uruguai.
    ids = [m.match_id for m in window]
    assert ids[0] == "A11"
    assert all(
        date(2026, 6, 11) <= parse_kickoff(m.kickoff_utc).date() < date(2026, 6, 16)
        for m in window
    )
    kicks = [parse_kickoff(m.kickoff_utc) for m in window]
    assert kicks == sorted(kicks)
    # Janela cobrindo o torneio inteiro: 104 jogos (72 + 32).
    assert len(eng.matches_between(date(2026, 6, 11), days=40)) == 104


def test_knockout_matches_carry_kickoff():
    eng = PredictionEngine()
    for matches in eng.rounds.values():
        assert all(m.kickoff_utc for m in matches)
