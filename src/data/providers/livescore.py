"""Provedor de placares ao vivo via cliente MCP (ex.: holoduke/livescore-mcp).

Age como CLIENTE de um servidor MCP de placares (livescore-mcp expõe um endpoint
SSE em https://livescoremcp.com/sse, grátis e sem chave). Busca os jogos da Copa,
normaliza-os e mapeia-os para os nossos `match_id`, fechando a lacuna de uma
fonte de resultados AUTOMÁTICA (sem digitação manual nem ficheiro).

Protocolo REAL do servidor (validado ao vivo em 2026-06-10):
  • `get_day_fixtures(date='DD/MM/YYYY')` devolve TEXTO: um cabeçalho seguido de
    um JSON aninhado país -> ligas -> jogos. A Copa aparece como país
    "World Cup" / liga "FIFA World Cup".
  • Cada jogo usa `localteam`/`visitorteam`, placar em `scoretime` ("2 - 1") e
    `status` ("Not Started", "FT", "AET", ...).

Estratégia: percorre os dias do torneio (start_date..hoje, limitado a end_date),
filtra a liga da Copa e mapeia cada par não-ordenado de seleções para o nosso
`match_id` — na fase de grupos cada par joga exatamente uma vez, logo o par
identifica o jogo. Dias já encerrados são cacheados (resultado final não muda).

CAVEATS (produção):
  • Requer acesso de rede ao host MCP.
  • Mapeia apenas a FASE DE GRUPOS (o par de seleções pode repetir-se nas
    eliminatórias); resultados de mata-mata entram via `update_real_score`.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from ...model.schemas import Match, Team
from .base import DataProvider
from .static import StaticProvider

# Janela da fase de grupos da Copa 2026 (a única que mapeamos por par).
_GROUP_STAGE_START = date(2026, 6, 11)
_GROUP_STAGE_END = date(2026, 6, 27)

# Nome (inglês/comum) -> código interno, para resolver as seleções do feed.
_NAME_ALIASES: dict[str, list[str]] = {
    "MEX": ["mexico"], "RSA": ["south africa"], "KOR": ["south korea", "korea republic", "korea"],
    "CAN": ["canada"], "SUI": ["switzerland"], "QAT": ["qatar"],
    "BRA": ["brazil"], "MAR": ["morocco"], "HAI": ["haiti"], "SCO": ["scotland"],
    "USA": ["united states", "usa", "united states of america"], "PAR": ["paraguay"],
    "AUS": ["australia"], "GER": ["germany"], "ECU": ["ecuador"],
    "CIV": ["ivory coast", "cote d'ivoire", "côte d'ivoire"], "CUW": ["curacao", "curaçao"],
    "NED": ["netherlands", "holland"], "JPN": ["japan"], "TUN": ["tunisia"],
    "BEL": ["belgium"], "IRN": ["iran", "ir iran"], "EGY": ["egypt"], "NZL": ["new zealand"],
    "ESP": ["spain"], "URU": ["uruguay"], "KSA": ["saudi arabia"], "CPV": ["cape verde", "cabo verde"],
    "FRA": ["france"], "SEN": ["senegal"], "NOR": ["norway"], "ARG": ["argentina"],
    "AUT": ["austria"], "ALG": ["algeria"], "JOR": ["jordan"], "POR": ["portugal"],
    "COL": ["colombia"], "UZB": ["uzbekistan"], "ENG": ["england"], "CRO": ["croatia"],
    "GHA": ["ghana"], "PAN": ["panama"],
    "CZE": ["czechia", "czech republic"], "BIH": ["bosnia and herzegovina", "bosnia-herzegovina", "bosnia"],
    "TUR": ["turkey", "türkiye", "turkiye"], "SWE": ["sweden"],
    "IRQ": ["iraq"], "COD": ["dr congo", "congo dr", "dr. congo", "democratic republic of congo", "congo"],
}


def _norm(name: str) -> str:
    return name.strip().lower()


class LiveScoreMcpProvider(DataProvider):
    def __init__(
        self,
        server_url: str = "https://livescoremcp.com/sse",
        base: DataProvider | None = None,
        fixtures_tool: str = "get_day_fixtures",
        competition: str = "FIFA World Cup",
        start_date: date = _GROUP_STAGE_START,
        end_date: date = _GROUP_STAGE_END,
        client_factory: Optional[Callable] = None,
    ):
        self.server_url = server_url
        self.base = base or StaticProvider()
        self.fixtures_tool = fixtures_tool
        self.competition = competition
        self.start_date = start_date
        self.end_date = end_date
        # Permite injetar um cliente in-memory nos testes; senão liga ao host.
        self._client_factory = client_factory or self._default_client_factory
        self._name_to_id = self._build_name_index()
        self._pair_index = self._build_pair_index()
        # Cache por dia encerrado: o resultado final de ontem não muda.
        self._day_cache: dict[str, list[dict]] = {}

    # -- delegação ao provedor base (seleções e calendário) ------------------
    def load_teams(self) -> dict[str, Team]:
        return self.base.load_teams()

    def load_group_fixtures(self) -> list[Match]:
        return self.base.load_group_fixtures()

    # -- índices de mapeamento ----------------------------------------------
    def _build_name_index(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for code, aliases in _NAME_ALIASES.items():
            index[_norm(code)] = code
            for alias in aliases:
                index[_norm(alias)] = code
        return index

    def _build_pair_index(self) -> dict[frozenset, tuple[str, str]]:
        """{id1,id2} -> (match_id, id_da_casa) para os jogos de grupo."""
        index: dict[frozenset, tuple[str, str]] = {}
        for m in self.base.load_group_fixtures():
            index[frozenset((m.home_team, m.away_team))] = (m.match_id, m.home_team)
        return index

    def _resolve_team(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        return self._name_to_id.get(_norm(name))

    # -- normalização da resposta do feed -----------------------------------
    @staticmethod
    def parse_match(raw: dict) -> Optional[dict]:
        """Extrai (home, away, gols, terminado) de um item do feed.

        Tolerante a variações de campos: aceita tanto o formato real do
        livescore-mcp (`localteam`/`visitorteam`/`scoretime`) como formatos
        genéricos (`home_team`/`home_score`).
        """
        home = (raw.get("home_team") or raw.get("home") or raw.get("home_name")
                or raw.get("localteam"))
        away = (raw.get("away_team") or raw.get("away") or raw.get("away_name")
                or raw.get("visitorteam"))
        hg = raw.get("home_score", raw.get("home_goals"))
        ag = raw.get("away_score", raw.get("away_goals"))
        if hg is None or ag is None:
            score = re.match(r"\s*(\d+)\s*-\s*(\d+)\s*$", str(raw.get("scoretime", "")))
            if score:
                hg, ag = int(score.group(1)), int(score.group(2))
        status = str(raw.get("status", "")).lower()
        finished = status in {
            "ft", "finished", "full-time", "fulltime", "ended", "aet",
        } or status.startswith(("ft", "aet", "after"))  # "After Pen." etc.
        if home is None or away is None:
            return None
        return {"home": home, "away": away, "hg": hg, "ag": ag, "finished": finished}

    @staticmethod
    def _extract_payload(result) -> list | dict:
        """Resposta -> JSON: o servidor real devolve TEXTO com JSON embutido."""
        if result.data is not None and not isinstance(result.data, str):
            return result.data
        texts = [result.data] if isinstance(result.data, str) else []
        texts += [getattr(block, "text", "") or "" for block in result.content or []]
        for text in texts:
            start = text.find("[")
            if start == -1:
                continue
            try:
                return json.loads(text[start:])
            except ValueError:
                continue
        return []

    def _matches_of_competition(self, payload: list | dict) -> list[dict]:
        """Achata país -> ligas -> jogos, filtrando a liga da Copa."""
        if isinstance(payload, dict):  # formato plano (mocks/feeds genéricos)
            return payload.get("matches") or payload.get("fixtures") or []
        wanted = _norm(self.competition)
        out: list[dict] = []
        for country in payload or []:
            if not isinstance(country, dict):
                continue
            for league in country.get("leagues", []) or []:
                label = _norm(f"{league.get('league', '')} {country.get('country', '')}")
                if wanted in label:
                    out.extend(league.get("matches", []) or [])
        return out

    # -- ligação MCP --------------------------------------------------------
    def _default_client_factory(self):
        from fastmcp import Client

        return Client(self.server_url)

    def _today(self) -> date:
        return datetime.now(timezone.utc).date()

    async def _afetch(self) -> list[dict]:
        """Jogos da Copa de todos os dias já decorridos da fase de grupos."""
        last_day = min(self._today(), self.end_date)
        if last_day < self.start_date:
            return []  # torneio ainda não começou
        days = [
            self.start_date + timedelta(days=i)
            for i in range((last_day - self.start_date).days + 1)
        ]
        per_day: dict[str, list[dict]] = {}
        to_fetch = [d for d in days if d.strftime("%d/%m/%Y") not in self._day_cache]
        if to_fetch:
            async with self._client_factory() as client:
                for day in to_fetch:
                    key = day.strftime("%d/%m/%Y")
                    result = await client.call_tool(self.fixtures_tool, {"date": key})
                    per_day[key] = self._matches_of_competition(self._extract_payload(result))
                    if day < self._today():  # dia encerrado: resultado é final
                        self._day_cache[key] = per_day[key]
        return [
            m
            for day in days
            for key in (day.strftime("%d/%m/%Y"),)
            for m in per_day.get(key, self._day_cache.get(key, []))
        ]

    def _run(self, coro):
        """Executa uma corrotina mesmo se já houver um event loop a correr."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # Há loop ativo (ex.: dentro do servidor MCP): corre noutra thread.
        box: dict = {}
        def worker():
            box["r"] = asyncio.run(coro)
        t = threading.Thread(target=worker)
        t.start(); t.join()
        return box["r"]

    # -- API do DataProvider -------------------------------------------------
    def fetch_results(self) -> dict[str, tuple[int, int]]:
        raw_matches = self._run(self._afetch())
        results: dict[str, tuple[int, int]] = {}
        for raw in raw_matches:
            parsed = self.parse_match(raw)
            if not parsed or not parsed["finished"]:
                continue
            home_id = self._resolve_team(parsed["home"])
            away_id = self._resolve_team(parsed["away"])
            if not home_id or not away_id or parsed["hg"] is None or parsed["ag"] is None:
                continue
            entry = self._pair_index.get(frozenset((home_id, away_id)))
            if entry is None:
                continue  # par não pertence à fase de grupos (ou desconhecido)
            match_id, our_home = entry
            hg, ag = int(parsed["hg"]), int(parsed["ag"])
            # Orienta o placar para o nosso lado de casa.
            results[match_id] = (hg, ag) if home_id == our_home else (ag, hg)
        return results
