"""Provedor de placares ao vivo via cliente MCP (ex.: holoduke/livescore-mcp).

Age como CLIENTE de um servidor MCP de placares (livescore-mcp expõe um endpoint
SSE em https://livescoremcp.com/sse, grátis e sem chave). Busca os jogos da Copa,
normaliza-os e mapeia-os para os nossos `match_id`, fechando a lacuna de uma
fonte de resultados AUTOMÁTICA (sem digitação manual nem ficheiro).

Estratégia de mapeamento (robusta para a fase de grupos): cada par de seleções
joga exatamente uma vez na fase de grupos, logo o par não-ordenado {id1,id2}
identifica unicamente o nosso jogo. Os nomes do feed são resolvidos para os
nossos códigos via uma tabela de aliases.

CAVEATS (produção):
  • Requer acesso de rede ao host MCP (bloqueado na sandbox; testado aqui contra
    um servidor mock in-memory através de `client_factory`).
  • O formato exato da resposta do feed pode variar — `parse_match` é o ponto
    único a ajustar à resposta real.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Callable, Optional

from ...model.schemas import Match, Team
from .base import DataProvider
from .static import StaticProvider

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
}


def _norm(name: str) -> str:
    return name.strip().lower()


class LiveScoreMcpProvider(DataProvider):
    def __init__(
        self,
        server_url: str = "https://livescoremcp.com/sse",
        base: DataProvider | None = None,
        fixtures_tool: str = "get_fixtures",
        competition: str = "World Cup",
        client_factory: Optional[Callable] = None,
    ):
        self.server_url = server_url
        self.base = base or StaticProvider()
        self.fixtures_tool = fixtures_tool
        self.competition = competition
        # Permite injetar um cliente in-memory nos testes; senão liga ao host.
        self._client_factory = client_factory or self._default_client_factory
        self._name_to_id = self._build_name_index()
        self._pair_index = self._build_pair_index()

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

        Tolerante a variações de campos. ESTE é o ponto a ajustar à resposta
        real do servidor de placares.
        """
        home = raw.get("home_team") or raw.get("home") or raw.get("home_name")
        away = raw.get("away_team") or raw.get("away") or raw.get("away_name")
        hg = raw.get("home_score", raw.get("home_goals"))
        ag = raw.get("away_score", raw.get("away_goals"))
        status = str(raw.get("status", "")).lower()
        finished = status in {"ft", "finished", "full-time", "fulltime", "ended", "aet"}
        if home is None or away is None:
            return None
        return {"home": home, "away": away, "hg": hg, "ag": ag, "finished": finished}

    # -- ligação MCP --------------------------------------------------------
    def _default_client_factory(self):
        from fastmcp import Client

        return Client(self.server_url)

    async def _afetch(self) -> list[dict]:
        async with self._client_factory() as client:
            result = await client.call_tool(
                self.fixtures_tool, {"competition": self.competition}
            )
        data = result.data
        if isinstance(data, dict):
            return data.get("matches") or data.get("fixtures") or []
        return data or []

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
