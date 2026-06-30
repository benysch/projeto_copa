"""Briefing diário da Copa 2026 via Telegram.

Gera as expectativas completas dos jogos DO DIA (horário de São Paulo) e envia
para um chat do Telegram: placar previsto, probabilidades 1X2, grau de
confiança, placares mais prováveis e o palpite ótimo para cada bolão
(pragma e bcf). Usa o mesmo `PredictionEngine` e provedor do servidor MCP,
pelo que os resultados reais já disputados entram no cálculo automaticamente
(WC2026_PROVIDER=livescore). Por padrão a engine é ancorada no mercado
(Polymarket) com peso 0.5 antes de gerar o briefing; se a calibração falhar
(rede), degrada para o modelo puro sem abortar o envio.

Executar:
    python -m src.daily_briefing                 # jogos de hoje, envia ao chat
    python -m src.daily_briefing --dry-run       # imprime sem enviar
    python -m src.daily_briefing --no-calibrate  # modelo puro, sem mercado
    python -m src.daily_briefing --date 2026-06-15
    python -m src.daily_briefing --chat-id       # descobre o chat_id (getUpdates)

Configuração (variáveis de ambiente ou ficheiro .env na raiz do repo):
    TELEGRAM_BOT_TOKEN          token do @BotFather
    TELEGRAM_CHAT_ID            chat de destino (use --chat-id para descobrir)
    WC2026_PROVIDER            fonte de dados (default: livescore)
    WC2026_CALIBRATION_WEIGHT  peso do modelo no blend (default: 0.5; 1.0 = puro)
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .data.calendar import parse_kickoff
from .data.providers import build_provider
from .model import bolao
from .model.bracket import (
    FINAL,
    QUARTER_FINALS,
    ROUND_OF_16,
    SEMI_FINALS,
    THIRD_PLACE,
)
from .model.schemas import Match, Phase
from .model.simulator import expected_goals, score_matrix
from .service.engine import PredictionEngine

# Jogo eliminatório -> refs (Wnn/Lnn) dos jogos que definem os seus dois
# participantes. Permite saber se o confronto JÁ É REAL (alimentadores
# disputados) ou ainda é projetado pelo modelo.
_KO_FEEDERS: dict[int, tuple[str, str]] = {
    num: (home_ref, away_ref)
    for num, home_ref, away_ref in (
        *ROUND_OF_16, *QUARTER_FINALS, *SEMI_FINALS, THIRD_PLACE, FINAL,
    )
}

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TZ = ZoneInfo("America/Sao_Paulo")
_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_MESSAGE_CHARS = 3900  # limite real é 4096; margem para o cabeçalho

_PHASE_LABELS = {
    Phase.GROUP_STAGE: "Fase de grupos",
    Phase.ROUND_OF_32: "32-avos de final",
    Phase.ROUND_OF_16: "Oitavas de final",
    Phase.QUARTER_FINALS: "Quartas de final",
    Phase.SEMI_FINALS: "Semifinal",
    Phase.THIRD_PLACE: "Disputa do 3º lugar",
    Phase.FINAL: "FINAL",
}

_WEEKDAYS = [
    "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
    "sexta-feira", "sábado", "domingo",
]


def load_dotenv(path: Path) -> None:
    """Carrega KEY=VALUE de um .env simples sem sobrepor o ambiente."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


# ----------------------------------------------------------------------
# Seleção e formatação dos jogos
# ----------------------------------------------------------------------
def matches_on_local_date(engine: PredictionEngine, target: date) -> list[Match]:
    """Jogos (todas as fases) cujo kickoff cai em `target` no fuso de São Paulo."""
    pool = engine.group_matches + [
        m for matches in engine.rounds.values() for m in matches
    ]
    selected = [
        (parse_kickoff(m.kickoff_utc), m)
        for m in pool
        if m.kickoff_utc and parse_kickoff(m.kickoff_utc).astimezone(_TZ).date() == target
    ]
    selected.sort(key=lambda km: km[0])
    return [m for _, m in selected]


def confronto_confirmado(engine: PredictionEngine, m: Match) -> bool:
    """True se os DOIS participantes do jogo já estão definidos por jogos reais.

    Grupos: sempre. 32-avos: quando a fase de grupos terminou (a classificação
    fixa quem joga). Demais fases: quando os dois jogos alimentadores (Wnn/Lnn)
    já foram disputados. Caso contrário, o confronto ainda é projetado.
    """
    if m.phase is Phase.GROUP_STAGE:
        return True
    if m.phase is Phase.ROUND_OF_32:
        return all(gm.is_finished for gm in engine.group_matches)
    feeders = _KO_FEEDERS.get(int(m.match_id[1:]))
    if not feeders:
        return False
    by_id = {km.match_id: km for ms in engine.rounds.values() for km in ms}
    return all(
        (fed := by_id.get(f"m{ref[1:]}")) is not None and fed.is_finished
        for ref in feeders
    )


def _format_pick(pick: dict, home: str, away: str) -> str:
    h, a = pick["score"]
    out = f"<b>{h}-{a}</b> ({pick['expected_points']:.2f} pts)"
    if "advancer_home" in pick:
        out += f", avança {home if pick['advancer_home'] else away}"
    if "penalty_winner_home" in pick:
        out += f", pênaltis p/ {home if pick['penalty_winner_home'] else away}"
    return out


def format_match(engine: PredictionEngine, m: Match) -> str:
    """Bloco de texto (HTML do Telegram) com a expectativa completa de um jogo."""
    home_t, away_t = engine.teams[m.home_team], engine.teams[m.away_team]
    home, away = html.escape(home_t.name), html.escape(away_t.name)
    kickoff = parse_kickoff(m.kickoff_utc).astimezone(_TZ).strftime("%H:%M")

    where = _PHASE_LABELS[m.phase]
    if m.phase is Phase.GROUP_STAGE:
        where = f"Grupo {m.group}"
        if m.matchday:
            where += f" · rodada {m.matchday}"

    lines = [f"🕐 {kickoff} · {where}", f"<b>{home} x {away}</b>"]
    if not m.is_finished and not confronto_confirmado(engine, m):
        lines[-1] += " <i>(confronto previsto pelo modelo)</i>"

    if m.is_finished:
        lines.append(
            f"✅ Resultado: <b>{m.real_score.home_goals}-{m.real_score.away_goals}</b>"
        )
        return "\n".join(lines)

    p = m.prediction
    if p is not None:
        winner = engine.teams[p.expected_winner].name if p.expected_winner else "Empate"
        lines.append(
            f"Palpite do modelo: <b>{p.predicted_score.home_goals}-"
            f"{p.predicted_score.away_goals}</b> "
            f"({html.escape(winner)} · confiança {p.confidence_level:.0f}%)"
        )
        lines.append(
            f"1X2: {p.prob_home * 100:.0f}% / {p.prob_draw * 100:.0f}% / "
            f"{p.prob_away * 100:.0f}%"
        )
        if p.top_scorelines:
            tops = " · ".join(
                f"{h}-{a} ({prob * 100:.0f}%)"
                for (h, a), prob in p.top_scorelines[:3]
            )
            lines.append(f"Placares prováveis: {tops}")
        if m.phase is Phase.GROUP_STAGE and p.is_balanced:
            lines.append(
                f"⚖️ Equilibrado — sem favorito claro "
                f"(empate com {p.prob_draw * 100:.0f}%)"
            )

    # Palpites ótimos por valor esperado, um por bolão.
    matrix = score_matrix(*expected_goals(home_t, away_t, engine.params), engine.params)
    pragma = bolao.best_picks(matrix, "pragma", m.phase, top=1)[0]
    bcf = bolao.best_picks(matrix, "bcf", m.phase, top=1)[0]
    lines.append(
        f"🎯 Pragma: {_format_pick(pragma, home, away)} | "
        f"BCF: {_format_pick(bcf, home, away)}"
    )
    return "\n".join(lines)


def build_briefing(engine: PredictionEngine, target: date) -> list[str]:
    """Mensagens do dia (já divididas para caber no limite do Telegram)."""
    weekday = _WEEKDAYS[target.weekday()]
    matches = matches_on_local_date(engine, target)

    header = f"⚽️ <b>Copa 2026 — {weekday}, {target.strftime('%d/%m')}</b>"
    if not matches:
        return [f"{header}\n\nSem jogos nesse dia. 😴"]

    champion = engine.champion
    sub = f"{len(matches)} jogo{'s' if len(matches) != 1 else ''}"
    if champion:
        sub += f" · campeão previsto: {html.escape(engine.teams[champion].name)}"
    blocks = [f"{header}\n{sub}"]
    blocks += [format_match(engine, m) for m in matches]

    # Divide em mensagens <= limite, sem partir um jogo ao meio.
    messages: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > _MAX_MESSAGE_CHARS and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


# ----------------------------------------------------------------------
# Telegram
# ----------------------------------------------------------------------
def send_telegram(token: str, chat_id: str, text: str) -> None:
    resp = requests.post(
        _TELEGRAM_API.format(token=token, method="sendMessage"),
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram recusou a mensagem: {data}")


def print_chat_ids(token: str) -> None:
    """Mostra os chats vistos pelo bot (envie uma mensagem ao bot antes)."""
    resp = requests.get(
        _TELEGRAM_API.format(token=token, method="getUpdates"), timeout=30
    )
    updates = resp.json().get("result", [])
    if not updates:
        print(
            "Nenhuma conversa encontrada. Envie qualquer mensagem ao bot no "
            "Telegram e rode de novo."
        )
        return
    seen: dict[int, str] = {}
    for u in updates:
        chat = (u.get("message") or u.get("channel_post") or {}).get("chat")
        if chat:
            label = chat.get("title") or chat.get("first_name") or chat.get("username")
            seen[chat["id"]] = f"{label} ({chat['type']})"
    for cid, label in seen.items():
        print(f"chat_id={cid}  ->  {label}")


# ----------------------------------------------------------------------
# Calibração de mercado
# ----------------------------------------------------------------------
_DEFAULT_CALIBRATION_WEIGHT = 0.5  # peso do MODELO no blend (1.0 = modelo puro)


def calibrate_engine(engine: PredictionEngine, weight: float) -> None:
    """Ancora a engine no mercado, degradando para o modelo puro se falhar.

    A calibração busca a Polymarket (rede) e leva ~10-20s; um erro de rede não
    pode derrubar o briefing, então qualquer exceção apenas reverte para o
    modelo puro e avisa no stderr.
    """
    if weight >= 1.0:
        return  # 1.0 = modelo puro: calibrar não tem efeito, poupa a rede
    try:
        result = engine.calibrate_to_market(weight=weight)
        print(
            f"Calibração de mercado aplicada (peso modelo={weight}, "
            f"tv_distance={result['tv_distance_pct']:.2f} p.p., "
            f"{result['adjusted_teams']} seleções).",
            file=sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001 — fallback deliberado p/ modelo puro
        engine.reset_market_calibration()
        print(
            f"AVISO: calibração de mercado falhou ({exc!r}); "
            "briefing seguirá com o modelo puro.",
            file=sys.stderr,
        )


# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Briefing diário da Copa no Telegram")
    parser.add_argument("--date", help="data alvo (YYYY-MM-DD); default: hoje em SP")
    parser.add_argument(
        "--dry-run", action="store_true", help="imprime as mensagens sem enviar"
    )
    parser.add_argument(
        "--chat-id", action="store_true",
        help="lista os chat_ids vistos pelo bot e sai",
    )
    parser.add_argument(
        "--no-calibrate", action="store_true",
        help="usa o modelo puro, sem ancorar no mercado (Polymarket)",
    )
    args = parser.parse_args(argv)

    load_dotenv(_REPO_ROOT / ".env")
    os.environ.setdefault("WC2026_PROVIDER", "livescore")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    if args.chat_id:
        if not token:
            print("Defina TELEGRAM_BOT_TOKEN (.env ou ambiente).", file=sys.stderr)
            return 1
        print_chat_ids(token)
        return 0

    target = (
        date.fromisoformat(args.date) if args.date else datetime.now(_TZ).date()
    )
    engine = PredictionEngine(build_provider())
    if not args.no_calibrate:
        weight = float(
            os.environ.get("WC2026_CALIBRATION_WEIGHT", _DEFAULT_CALIBRATION_WEIGHT)
        )
        calibrate_engine(engine, weight)
    messages = build_briefing(engine, target)

    if args.dry_run:
        for msg in messages:
            print(msg)
            print("-" * 60)
        return 0

    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print(
            "Defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID (.env ou ambiente).",
            file=sys.stderr,
        )
        return 1
    for msg in messages:
        send_telegram(token, chat_id, msg)
    print(f"Briefing de {target.isoformat()} enviado ({len(messages)} mensagem(ns)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
