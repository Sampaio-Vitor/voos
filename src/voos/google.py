"""Google Flights pela swoop: busca, voltas de uma ida e link de compra na companhia.

O fluxo é o mesmo do site: só ida lista os voos; ida e volta lista as idas com o
preço total "a partir de", e só depois de escolher a ida aparecem as voltas com o
preço do par. O link de compra é o da própria companhia (as agências exigem um
token anti-bot do navegador e ficam de fora).
"""

from __future__ import annotations

import html
import re
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, NotRequired, TypedDict

from mcp.server.mcpserver.exceptions import ToolError
from primp import Client
from swoop import SORT_CHEAPEST, SORT_DEPARTURE_TIME, SORT_DURATION, Passengers, TransportConfig
from swoop import rpc as swoop_rpc
from swoop._selection import _iter_raw_itineraries, fetch_trip_booking_options
from swoop.decoder import Itinerary
from swoop.exceptions import SwoopError, SwoopRateLimitError

Cabine = Literal["economica", "premium", "executiva", "primeira"]
Ordem = Literal["preco", "partida", "duracao"]

CABINES: dict[str, str] = {
    "economica": "economy",
    "premium": "premium-economy",
    "executiva": "business",
    "primeira": "first",
}
ORDENS = {"preco": SORT_CHEAPEST, "partida": SORT_DEPARTURE_TIME, "duracao": SORT_DURATION}
IATA = re.compile(r"^[A-Z]{3}$")
COMPANHIA = re.compile(r"^[A-Z0-9]{2}$")
REDIRECT = re.compile(r"url='([^']+)'", re.IGNORECASE)


class ErroDeBusca(ToolError):
    """Mensagem pronta para o agente: diz o que fazer, não o detalhe interno. Como
    ToolError, o SDK repassa o texto; qualquer outra exceção chega só como falha."""


# Respostas das ferramentas. Tipadas para o MCP mandar também o JSON estruturado,
# que a tela do Tião pode desenhar sem passar pelo modelo.


class Conexao(TypedDict):
    aeroporto: str
    espera_min: int


class Voo(TypedDict):
    id: str
    preco: NotRequired[int]
    companhias: list[str]
    voos: list[str]
    de: str
    para: str
    partida: str
    chegada: str
    duracao_min: int
    escalas: int
    conexoes: list[Conexao]


class Busca(TypedDict):
    tipo: Literal["so_ida", "ida_e_volta"]
    moeda: str | None
    preco_quer_dizer: str
    proximo_passo: str
    voos: list[Voo]


class Voltas(TypedDict):
    tipo: Literal["voltas"]
    moeda: str | None
    preco_quer_dizer: str
    proximo_passo: str
    ida: Voo
    voos: list[Voo]


class Link(TypedDict):
    id: str
    companhia: str
    preco: int
    moeda: str | None
    tarifa: str | None
    link: str
    aviso: str


@dataclass(frozen=True)
class Filtros:
    adultos: int = 1
    criancas: int = 0
    bebes_no_colo: int = 0
    cabine: Cabine = "economica"
    max_escalas: int | None = None
    companhias: tuple[str, ...] = ()
    partida_apos: int | None = None
    partida_ate: int | None = None

    def passageiros(self) -> Passengers:
        return Passengers(
            adults=self.adultos, children=self.criancas, infants_on_lap=self.bebes_no_colo
        )


@dataclass
class Opcao:
    """Uma linha devolvida ao agente. `itinerarios` tem a ida e, no par, a volta."""

    pernas: list[dict[str, Any]]
    itinerarios: list[Itinerary]
    filtros: Filtros
    precisa_volta: bool = False
    criada: float = field(default_factory=lambda: time.monotonic())


class Catalogo:
    """IDs curtos para o agente. Os tokens do Google vencem, então guardar mais que
    uma hora não adianta; reiniciar o serviço só obriga a buscar de novo."""

    def __init__(self, validade_s: float = 3600, maximo: int = 5000):
        self.validade_s = validade_s
        self.maximo = maximo
        self._itens: OrderedDict[str, Opcao] = OrderedDict()
        self._trava = threading.Lock()

    def guardar(self, opcao: Opcao) -> str:
        with self._trava:
            while (novo := secrets.token_hex(4)) in self._itens:
                pass
            self._itens[novo] = opcao
            while len(self._itens) > self.maximo:
                self._itens.popitem(last=False)
            return novo

    def pegar(self, ident: str) -> Opcao:
        with self._trava:
            opcao = self._itens.get(ident)
            if opcao is None or time.monotonic() - opcao.criada > self.validade_s:
                self._itens.pop(ident, None)
                raise ErroDeBusca(f"O id {ident!r} não existe ou venceu. Faça a busca de novo.")
            return opcao


class Google:
    def __init__(self, catalogo: Catalogo | None = None, pais: str = "BR", timeout_s: int = 30):
        self.catalogo = catalogo or Catalogo()
        self.transporte = TransportConfig(timeout=timeout_s, retries=2, country=pais)
        self._http = Client(impersonate="chrome_145", impersonate_os="macos", timeout=timeout_s)

    # Busca

    def buscar(
        self,
        origem: str,
        destino: str,
        data: str,
        data_volta: str | None,
        filtros: Filtros,
        ordenar: Ordem,
        limite: int,
    ) -> Busca:
        origem, destino = _iata(origem, "origem"), _iata(destino, "destino")
        ida = _data(data, "data")
        _validar(filtros)
        pernas = [_perna(origem, destino, ida.isoformat(), filtros, partida=True)]
        if data_volta:
            volta = _data(data_volta, "data_volta")
            if volta < ida:
                raise ErroDeBusca("A data_volta é anterior à data de ida.")
            pernas.append(_perna(destino, origem, volta.isoformat(), filtros, partida=False))
        itinerarios = self._consultar(pernas, filtros, ordenar)
        voos = []
        for itin in _ordenar(itinerarios, ordenar)[:limite]:
            opcao = Opcao(pernas, [itin], filtros, precisa_volta=bool(data_volta))
            voos.append(_voo(self.catalogo.guardar(opcao), itin))
        return {
            "tipo": "ida_e_volta" if data_volta else "so_ida",
            "moeda": _moeda(itinerarios),
            "preco_quer_dizer": (
                "total da ida e volta, a partir de; a volta escolhida pode mudar o valor"
                if data_volta
                else "só a ida"
            ),
            "proximo_passo": (
                "opcoes_de_volta(id) com a ida escolhida"
                if data_volta
                else "link_de_compra(id) com o voo escolhido"
            ),
            "voos": voos,
        }

    def voltas(self, id_ida: str, ordenar: Ordem, limite: int) -> Voltas:
        opcao = self.catalogo.pegar(id_ida)
        if not opcao.precisa_volta:
            raise ErroDeBusca("Esse id não é de uma ida de ida e volta. Use link_de_compra(id).")
        ida = opcao.itinerarios[0]
        pernas = [dict(opcao.pernas[0]), dict(opcao.pernas[1])]
        pernas[0]["selected_legs"] = swoop_rpc._build_selected_legs(ida)
        itinerarios = self._consultar(pernas, opcao.filtros, ordenar)
        voos = []
        for itin in _ordenar(itinerarios, ordenar)[:limite]:
            par = Opcao(opcao.pernas, [ida, itin], opcao.filtros)
            voos.append(_voo(self.catalogo.guardar(par), itin))
        return {
            "tipo": "voltas",
            "moeda": _moeda(itinerarios),
            "preco_quer_dizer": "total da ida escolhida com esta volta",
            "proximo_passo": "link_de_compra(id) com a volta escolhida",
            "ida": _voo(id_ida, ida, com_preco=False),
            "voos": voos,
        }

    # Compra

    def link(self, ident: str) -> Link:
        opcao = self.catalogo.pegar(ident)
        if opcao.precisa_volta:
            raise ErroDeBusca("Falta escolher a volta: use opcoes_de_volta(id) antes do link.")
        try:
            ofertas = fetch_trip_booking_options(
                opcao.pernas,
                opcao.itinerarios,
                cabin=CABINES[opcao.filtros.cabine],
                passengers=opcao.filtros.passageiros(),
                transport=self.transporte,
            )
        except SwoopError as erro:
            raise _traduzir(erro) from erro
        diretas = [o for o in ofertas if o.is_airline_direct and o.booking_url and o.price > 0]
        if not diretas:
            raise ErroDeBusca(
                "O Google não devolveu oferta direta da companhia para esse voo. "
                "Pode ter esgotado: faça a busca de novo."
            )
        oferta = min(diretas, key=lambda o: o.price)
        return {
            "id": ident,
            "companhia": oferta.seller_name,
            "preco": oferta.price,
            "moeda": _moeda(opcao.itinerarios),
            "tarifa": oferta.brand_label or None,
            "link": self._resolver(oferta.booking_url),
            "aviso": "preço do momento; a companhia confirma o valor final no checkout",
        }

    def _resolver(self, url: str) -> str:
        """O link do Google é uma página com meta refresh para a companhia; devolver o
        destino evita o redirect e funciona fora do navegador. Se mudar, fica o do Google."""
        try:
            resposta = self._http.get(url)
        except Exception:
            return url
        destino = resposta.headers.get("location")
        if not destino and (achado := REDIRECT.search(resposta.text)):
            destino = achado.group(1)
        destino = html.unescape(destino or "")
        return destino if destino.startswith("https://") else url

    # Interno

    def _consultar(self, pernas, filtros: Filtros, ordenar: Ordem) -> list[Itinerary]:
        try:
            bruto = swoop_rpc._search_from_legs(
                pernas,
                cabin=CABINES[filtros.cabine],
                passengers=filtros.passageiros(),
                sort=ORDENS[ordenar],
                transport=self.transporte,
                retain_raw=False,
            )
        except SwoopError as erro:
            raise _traduzir(erro) from erro
        return [i for i in _iter_raw_itineraries(bruto) if i.price]


def _perna(origem: str, destino: str, dia: str, f: Filtros, partida: bool) -> dict[str, Any]:
    return swoop_rpc._normalize_rpc_leg(
        origem,
        destino,
        dia,
        max_stops=f.max_escalas,
        airlines=list(f.companhias) or None,
        earliest_departure=f.partida_apos if partida else None,
        latest_departure=f.partida_ate if partida else None,
    )


def _ordenar(itinerarios: list[Itinerary], ordenar: Ordem) -> list[Itinerary]:
    chave = {
        "preco": lambda i: (i.price, i.departure_date, i.departure_time),
        "partida": lambda i: (i.departure_date, i.departure_time, i.price),
        "duracao": lambda i: (i.travel_time, i.price),
    }[ordenar]
    return sorted(itinerarios, key=chave)


def _voo(ident: str, itin: Itinerary, com_preco: bool = True) -> Voo:
    voo = Voo(
        id=ident,
        companhias=itin.airline_names,
        voos=[f"{s.airline} {s.flight_number}" for s in itin.segments],
        de=itin.departure_airport_code,
        para=itin.arrival_airport_code,
        partida=_quando(itin.departure_date, itin.departure_time),
        chegada=_quando(itin.arrival_date, itin.arrival_time),
        duracao_min=itin.travel_time,
        escalas=itin.stop_count if itin.stop_count is not None else len(itin.layovers),
        conexoes=[
            Conexao(aeroporto=c.departure_airport_code, espera_min=c.minutes) for c in itin.layovers
        ],
    )
    # Na lista de voltas, a ida aparece só para conferência: o preço é o de cada par.
    if com_preco:
        voo["preco"] = itin.price
    return voo


def _quando(dia: tuple[int, int, int], hora: tuple[int, int]) -> str:
    return f"{dia[0]:04d}-{dia[1]:02d}-{dia[2]:02d}T{hora[0]:02d}:{hora[1]:02d}"


def _moeda(itinerarios: list[Itinerary]) -> str | None:
    return next((i.currency for i in itinerarios if i.currency), None)


def _iata(valor: str, campo: str) -> str:
    codigo = valor.strip().upper()
    if not IATA.match(codigo):
        raise ErroDeBusca(f"{campo} precisa ser um código IATA de 3 letras, como GRU ou CNF.")
    return codigo


def _data(valor: str, campo: str) -> date:
    try:
        dia = date.fromisoformat(valor)
    except ValueError:
        raise ErroDeBusca(f"{campo} precisa estar no formato AAAA-MM-DD.") from None
    if dia < date.today():
        raise ErroDeBusca(f"{campo} já passou.")
    return dia


def _validar(f: Filtros) -> None:
    if not 1 <= f.adultos <= 9 or f.criancas < 0 or f.bebes_no_colo < 0:
        raise ErroDeBusca("Passageiros: de 1 a 9 adultos, crianças e bebês não negativos.")
    if f.adultos + f.criancas + f.bebes_no_colo > 9:
        raise ErroDeBusca("O Google Flights aceita no máximo 9 passageiros.")
    if f.bebes_no_colo > f.adultos:
        raise ErroDeBusca("Cada bebê no colo precisa de um adulto.")
    if f.cabine not in CABINES:
        raise ErroDeBusca(f"cabine precisa ser uma de: {', '.join(CABINES)}.")
    if f.max_escalas is not None and not 0 <= f.max_escalas <= 2:
        raise ErroDeBusca("max_escalas vai de 0 (direto) a 2.")
    for hora in (f.partida_apos, f.partida_ate):
        if hora is not None and not 0 <= hora <= 23:
            raise ErroDeBusca("partida_apos e partida_ate são horas de 0 a 23.")
    for codigo in f.companhias:
        if not COMPANHIA.match(codigo):
            raise ErroDeBusca("companhias são códigos IATA de 2 caracteres, como G3, AD ou LA.")


def _traduzir(erro: SwoopError) -> ErroDeBusca:
    if isinstance(erro, SwoopRateLimitError):
        return ErroDeBusca("O Google limitou as consultas agora. Tente de novo em alguns minutos.")
    return ErroDeBusca(f"O Google Flights não respondeu como esperado ({type(erro).__name__}).")
