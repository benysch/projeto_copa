"""Bot conversacional da Copa 2026 no Telegram (@CopaAI_bot).

Complementa o briefing diário (src/daily_briefing.py): fica em long-polling no
getUpdates e responde a pedidos em português — "amanhã", "hoje", "dia 15",
"grupo B", "campeão", "elo", "jogos do Brasil" — usando o mesmo
`PredictionEngine` do servidor MCP. O estado é re-sincronizado com a fonte ao
vivo no máximo a cada `_REFRESH_SECONDS`, na chegada de uma mensagem.

Executar (normalmente via systemd, ver scripts/copabot.service):
    python -m src.telegram_bot

Configuração (.env na raiz do repo ou ambiente):
    TELEGRAM_BOT_TOKEN       token do @BotFather
    TELEGRAM_ALLOWED_CHATS   chat_ids autorizados, separados por vírgula
                             (default: TELEGRAM_CHAT_ID)
"""

from __future__ import annotations

import html
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta

import requests

from .daily_briefing import (
    _REPO_ROOT,
    _TELEGRAM_API,
    _TZ,
    build_briefing,
    format_match,
    load_dotenv,
    send_telegram,
)
from .data.calendar import parse_kickoff
from .data.providers import build_provider
from .model.schemas import Match, Phase
from .service.engine import PredictionEngine

_REFRESH_SECONDS = 600  # idade máxima do estado antes de re-sincronizar
_POLL_TIMEOUT = 50

HELP = """🤖 <b>CopaAI — o que sei responder</b>

• <b>hoje</b> / <b>amanhã</b> / <b>ontem</b> — jogos do dia com palpites
• <b>dia 15</b> ou <b>15/06</b> — jogos de uma data
• <b>grupo B</b> — classificação prevista do grupo
• <b>campeão</b> — probabilidades de título (Monte Carlo)
• <b>elo</b> — ranking de força atual
• <b>Brasil</b> (nome de seleção) — próximos jogos dela

Os palpites 🎯 são os de maior valor esperado em cada bolão (Pragma e BCF).

<i>Admin:</i> <code>/resultado m75 2 1</code> registra um placar real
(<code>m74 1 1 pen PAR</code> para pênaltis; <code>A11 3 0</code> para grupos)."""


def _normalize(text: str) -> str:
    """minúsculas + sem acentos, para casar 'amanhã', 'campeao', etc."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


class Bot:
    def __init__(
        self, token: str, allowed_chats: set[int], admin_chats: set[int] | None = None
    ):
        self.token = token
        self.allowed_chats = allowed_chats
        self.admin_chats = admin_chats or set()
        self.engine = PredictionEngine(build_provider())
        self._last_refresh = time.monotonic()

    # ------------------------------------------------------------------
    def fresh_engine(self) -> PredictionEngine:
        """Engine com resultados reais não mais velhos que _REFRESH_SECONDS."""
        if time.monotonic() - self._last_refresh > _REFRESH_SECONDS:
            try:
                self.engine.refresh()
                self._last_refresh = time.monotonic()
            except Exception:
                pass  # fonte fora do ar: responde com o último estado bom
        return self.engine

    # ------------------------------------------------------------------
    # Respostas
    # ------------------------------------------------------------------
    def answer(self, text: str) -> list[str]:
        t = _normalize(text)
        today = datetime.now(_TZ).date()

        if t.startswith("/start") or "ajuda" in t or "help" in t or t == "/help":
            return [HELP]

        if "amanha" in t:
            return build_briefing(self.fresh_engine(), today + timedelta(days=1))
        if "ontem" in t:
            return build_briefing(self.fresh_engine(), today - timedelta(days=1))
        if "hoje" in t or t in ("jogos", "/jogos"):
            return build_briefing(self.fresh_engine(), today)

        m = re.search(r"\b(\d{1,2})/(\d{1,2})\b", t)
        if m:
            day, month = int(m.group(1)), int(m.group(2))
            return self._briefing_for(date(2026, month, day))
        m = re.search(r"\bdia (\d{1,2})\b", t)
        if m:
            day = int(m.group(1))
            month = today.month if day >= today.day else today.month + 1
            return self._briefing_for(date(2026, month, day))

        m = re.search(r"\bgrupo ([a-l])\b", t)
        if m:
            return [self._standings(m.group(1).upper())]

        if "campeao" in t or "titulo" in t or "probabilidade" in t:
            return [self._title_probabilities()]

        if "elo" in t or "ranking" in t or "forca" in t:
            return [self._elo_ratings()]

        team_msgs = self._team_lookup(t)
        if team_msgs:
            return team_msgs

        return [HELP]

    # ------------------------------------------------------------------
    # Atualização da base (admin): /resultado <jogo> <casa> <fora> [pen <time>]
    # ------------------------------------------------------------------
    _USAGE = (
        "Uso: <code>/resultado &lt;jogo&gt; &lt;casa&gt; &lt;fora&gt; [pen &lt;time&gt;]</code>\n"
        "Ex.: <code>/resultado m75 2 1</code> · "
        "<code>/resultado A11 3 0</code> · "
        "<code>/resultado m74 1 1 pen PAR</code>"
    )

    @staticmethod
    def _canonical_match_id(raw: str) -> str:
        """Normaliza o id: 'a11'->'A11' (grupo), 'M73'/'m73'->'m73' (mata-mata)."""
        s = raw.strip()
        if re.fullmatch(r"[mM]\d{1,3}", s):
            return "m" + s[1:]
        return s.upper()

    def _resolve_team(self, match: Match, token: str) -> str | None:
        """Resolve um token (código ou nome) para o team_id de um dos dois times."""
        tok = _normalize(token)
        for tid in (match.home_team, match.away_team):
            if tok == tid.lower() or tok in _normalize(self.engine.teams[tid].name):
                return tid
        return None

    def _update_result(self, text: str) -> str:
        parts = text.split()
        if len(parts) < 4:
            return self._USAGE
        mid = self._canonical_match_id(parts[1])
        try:
            hg, ag = int(parts[2]), int(parts[3])
        except ValueError:
            return "Placar inválido. " + self._USAGE
        if hg < 0 or ag < 0:
            return "Placar não pode ser negativo. " + self._USAGE

        pen_token: str | None = None
        if len(parts) >= 5:
            if _normalize(parts[4]) != "pen" or len(parts) < 6:
                return "Pênaltis: use <code>… pen &lt;time&gt;</code>. " + self._USAGE
            pen_token = parts[5]

        match = self.engine._find_match(mid)
        if match is None:
            return (
                f"Jogo <b>{html.escape(parts[1])}</b> não existe. Ids: "
                f"<code>A11</code> (grupo) ou <code>m73</code>–<code>m104</code> (mata-mata)."
            )

        pen: str | None = None
        if pen_token is not None:
            pen = self._resolve_team(match, pen_token)
            if pen is None:
                opts = f"{match.home_team}/{match.away_team}"
                return f"Time '{html.escape(pen_token)}' não joga em {mid} ({opts})."

        try:
            m = self.engine.update_real_score(
                mid, hg, ag, penalty_winner=pen, persist=True
            )
        except (KeyError, ValueError) as exc:
            return f"Não consegui registrar: {html.escape(str(exc))}"
        self._last_refresh = time.monotonic()

        h = html.escape(self.engine.teams[m.home_team].name)
        a = html.escape(self.engine.teams[m.away_team].name)
        out = f"✅ <b>{m.match_id}</b>: {h} <b>{hg}-{ag}</b> {a} registrado."
        if pen:
            out += f"\n🥅 Pênaltis: avança <b>{html.escape(self.engine.teams[pen].name)}</b>."
        elif hg == ag and match.phase is not Phase.GROUP_STAGE:
            out += (
                "\n⚠️ Empate em mata-mata sem <code>pen &lt;time&gt;</code>: "
                "o modelo usará o favorito para quem avança."
            )
        return out

    def _briefing_for(self, target: date) -> list[str]:
        try:
            return build_briefing(self.fresh_engine(), target)
        except ValueError:
            return ["Data inválida. 🤔 Tente algo como <b>dia 15</b> ou <b>15/06</b>."]

    def _standings(self, group: str) -> str:
        engine = self.fresh_engine()
        if engine.standings is None or group not in engine.standings.tables:
            return f"Grupo {group}? Não conheço. Use A–L."
        lines = [f"📊 <b>Grupo {group} — classificação prevista</b>"]
        for pos, row in enumerate(engine.standings.tables[group], start=1):
            mark = "🟢" if pos <= 2 else "▫️"
            name = html.escape(engine.teams[row.team_id].name)
            lines.append(
                f"{mark} {pos}. {name} — {row.points} pts "
                f"(J{row.played}, SG {row.goal_difference:+d})"
            )
        lines.append("🟢 = classificação direta prevista")
        return "\n".join(lines)

    def _title_probabilities(self) -> str:
        engine = self.fresh_engine()
        result = engine.probabilities(n_sims=2000)
        lines = ["🏆 <b>Probabilidades de título</b> (2.000 simulações)"]
        for tid, probs in result.table(engine.teams, top=10):
            name = html.escape(engine.teams[tid].name)
            lines.append(
                f"• {name}: <b>{probs['champion']:.1f}%</b> "
                f"(final: {probs['final']:.0f}%)"
            )
        return "\n".join(lines)

    def _elo_ratings(self) -> str:
        engine = self.fresh_engine()
        ranked = sorted(engine.teams.values(), key=lambda x: x.elo, reverse=True)
        lines = ["📈 <b>Ranking Elo atual</b> (Δ desde o início da Copa)"]
        for i, team in enumerate(ranked[:12], start=1):
            delta = engine.elo_delta(team.team_id)
            lines.append(
                f"{i}. {html.escape(team.name)} — {team.elo:.0f} ({delta:+.0f})"
            )
        return "\n".join(lines)

    def _team_lookup(self, t: str) -> list[str] | None:
        """Se a mensagem cita uma seleção, devolve os próximos jogos dela."""
        engine = self.fresh_engine()
        team_id = None
        for tid, team in engine.teams.items():
            if _normalize(team.name) in t or tid.lower() in t.split():
                team_id = tid
                break
        if team_id is None:
            return None
        pool = engine.group_matches + [
            m for ms in engine.rounds.values() for m in ms
        ]
        upcoming = sorted(
            (m for m in pool
             if team_id in (m.home_team, m.away_team)
             and not m.is_finished and m.kickoff_utc),
            key=lambda m: parse_kickoff(m.kickoff_utc),
        )[:3]
        if not upcoming:
            return [f"Sem jogos futuros previstos para "
                    f"{html.escape(engine.teams[team_id].name)}."]
        name = html.escape(engine.teams[team_id].name)
        blocks = [f"📅 <b>Próximos jogos — {name}</b>"]
        for m in upcoming:
            dt = parse_kickoff(m.kickoff_utc).astimezone(_TZ)
            blocks.append(f"<i>{dt.strftime('%d/%m')}</i>\n{format_match(engine, m)}")
        return ["\n\n".join(blocks)]

    # ------------------------------------------------------------------
    # Loop de polling
    # ------------------------------------------------------------------
    def run(self) -> None:
        print("CopaAI bot escutando…", flush=True)
        offset: int | None = None
        while True:
            try:
                resp = requests.get(
                    _TELEGRAM_API.format(token=self.token, method="getUpdates"),
                    params={"timeout": _POLL_TIMEOUT, "offset": offset},
                    timeout=_POLL_TIMEOUT + 10,
                )
                updates = resp.json().get("result", [])
            except Exception as exc:
                print(f"polling falhou ({exc}); tentando de novo em 10s", flush=True)
                time.sleep(10)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                self._handle(update)

    def _handle(self, update: dict) -> None:
        message = update.get("message") or update.get("channel_post")
        if not message or "text" not in message:
            return
        chat_id = message["chat"]["id"]
        if self.allowed_chats and chat_id not in self.allowed_chats:
            self._reply(chat_id, "🔒 Bot privado da família Schiavoni.")
            return
        text = message["text"]
        if _normalize(text).lstrip().startswith("/resultado"):
            if chat_id not in self.admin_chats:
                self._reply(chat_id, "🔒 Só admin pode atualizar resultados.")
                return
            self._reply(chat_id, self._update_result(text))
            return
        try:
            replies = self.answer(text)
        except Exception as exc:
            print(f"erro respondendo a {message['text']!r}: {exc}", flush=True)
            replies = ["⚠️ Deu erro aqui do meu lado. Tente de novo em instantes."]
        for reply in replies:
            self._reply(chat_id, reply)

    def _reply(self, chat_id: int, text: str) -> None:
        try:
            send_telegram(self.token, str(chat_id), text)
        except Exception as exc:
            print(f"falha ao enviar para {chat_id}: {exc}", flush=True)


def main() -> int:
    load_dotenv(_REPO_ROOT / ".env")
    os.environ.setdefault("WC2026_PROVIDER", "livescore")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("Defina TELEGRAM_BOT_TOKEN (.env ou ambiente).", file=sys.stderr)
        return 1
    raw = os.environ.get(
        "TELEGRAM_ALLOWED_CHATS", os.environ.get("TELEGRAM_CHAT_ID", "")
    )
    allowed = {int(c) for c in raw.split(",") if c.strip()}
    # Admins de ESCRITA (/resultado): default = TELEGRAM_CHAT_ID. Nunca vazio
    # à toa — um conjunto vazio aqui significaria "ninguém pode atualizar".
    admin_raw = os.environ.get(
        "TELEGRAM_ADMIN_CHATS", os.environ.get("TELEGRAM_CHAT_ID", "")
    )
    admins = {int(c) for c in admin_raw.split(",") if c.strip()}
    Bot(token, allowed, admins).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
