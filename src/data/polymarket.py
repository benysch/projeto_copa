"""Probabilidades implícitas do Polymarket — visão do MERCADO sobre a Copa.

O evento `world-cup-winner` da Gamma API agrega um mercado binário por seleção
("Will X win the 2026 FIFA World Cup?"). Os preços (0–1) são probabilidades
implícitas com sobre-preço (vig ~4%): normalizamos sobre as 48 classificadas
para obter uma distribuição comparável à do nosso Monte Carlo.

O fetch é separado do parse: testes usam payloads fixos, sem rede. Cache de
5 minutos para não martelar a API a cada chamada de ferramenta.
"""

from __future__ import annotations

import json
import time

GAMMA_API = "https://gamma-api.polymarket.com"
TITLE_EVENT_SLUG = "world-cup-winner"
_CACHE_TTL_SECONDS = 300.0

# Nome em inglês (como aparece nas perguntas do Polymarket) -> team_id FIFA.
# Inclui variantes/grafias alternativas observadas em mercados esportivos.
_NAME_TO_ID: dict[str, str] = {
    "mexico": "MEX", "south africa": "RSA", "south korea": "KOR",
    "korea republic": "KOR", "czechia": "CZE", "czech republic": "CZE",
    "canada": "CAN", "switzerland": "SUI", "qatar": "QAT",
    "bosnia-herzegovina": "BIH", "bosnia and herzegovina": "BIH",
    "brazil": "BRA", "morocco": "MAR", "haiti": "HAI", "scotland": "SCO",
    "usa": "USA", "united states": "USA", "paraguay": "PAR",
    "australia": "AUS", "turkey": "TUR", "turkiye": "TUR", "türkiye": "TUR",
    "germany": "GER", "ecuador": "ECU", "ivory coast": "CIV",
    "cote d'ivoire": "CIV", "curacao": "CUW", "curaçao": "CUW",
    "netherlands": "NED", "japan": "JPN", "tunisia": "TUN", "sweden": "SWE",
    "belgium": "BEL", "iran": "IRN", "egypt": "EGY", "new zealand": "NZL",
    "spain": "ESP", "uruguay": "URU", "saudi arabia": "KSA",
    "cape verde": "CPV", "cabo verde": "CPV", "france": "FRA",
    "senegal": "SEN", "norway": "NOR", "iraq": "IRQ", "argentina": "ARG",
    "austria": "AUT", "algeria": "ALG", "jordan": "JOR", "portugal": "POR",
    "colombia": "COL", "uzbekistan": "UZB", "dr congo": "COD",
    "democratic republic of the congo": "COD", "england": "ENG",
    "croatia": "CRO", "ghana": "GHA", "panama": "PAN",
}

_QUESTION_PREFIX = "will "
_QUESTION_SUFFIX = " win the 2026 fifa world cup?"

_cache: dict[str, tuple[float, dict]] = {}


def fetch_event(slug: str = TITLE_EVENT_SLUG) -> dict:
    """Busca um evento da Gamma API (com cache de 5 minutos)."""
    now = time.monotonic()
    hit = _cache.get(slug)
    if hit and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]

    import requests  # import local: dependência só é exigida quando usada

    resp = requests.get(
        f"{GAMMA_API}/events", params={"slug": slug}, timeout=30
    )
    resp.raise_for_status()
    payload = resp.json()
    if not payload:
        raise ValueError(f"Evento Polymarket não encontrado: '{slug}'")
    event = payload[0]
    _cache[slug] = (now, event)
    return event


def parse_title_prices(event: dict) -> dict[str, float]:
    """Extrai {team_id: preço 'Yes'} dos mercados do evento de campeão.

    Perguntas que não casam com uma seleção classificada (ex.: mercados de
    seleções eliminadas nas eliminatórias) são ignoradas.
    """
    prices: dict[str, float] = {}
    for market in event.get("markets", []):
        question = (market.get("question") or "").strip().lower()
        if not (
            question.startswith(_QUESTION_PREFIX)
            and question.endswith(_QUESTION_SUFFIX)
        ):
            continue
        name = question[len(_QUESTION_PREFIX): -len(_QUESTION_SUFFIX)].strip()
        team_id = _NAME_TO_ID.get(name)
        if team_id is None:
            continue
        try:
            raw = json.loads(market.get("outcomePrices") or "[]")
            price = float(raw[0])
        except (json.JSONDecodeError, ValueError, IndexError, TypeError):
            continue
        # Mercados duplicados (relistagens): fica o de maior preço informado.
        prices[team_id] = max(price, prices.get(team_id, 0.0))
    return prices


def implied_title_probabilities(
    event: dict | None = None,
) -> tuple[dict[str, float], float]:
    """Probabilidades de título implícitas no mercado, sem o vig (soma = 1).

    `event` permite injetar um payload (testes); default busca da API.
    Devolve (probabilidades, vig_pct), onde vig_pct é o sobre-preço bruto.
    """
    event = event if event is not None else fetch_event()
    prices = parse_title_prices(event)
    if not prices:
        raise ValueError(
            "Nenhum mercado de campeão reconhecido no evento do Polymarket."
        )
    total = sum(prices.values())
    probs = {tid: p / total for tid, p in prices.items()}
    return probs, round((total - 1.0) * 100, 1)
