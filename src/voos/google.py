"""Google Flights: busca, voltas de uma ida e link de compra na companhia.

O fluxo é o mesmo do site: só ida lista os voos; ida e volta lista as idas com o
preço total "a partir de", e só depois de escolher a ida aparecem as voltas com o
preço do par.

A busca lê a própria página de resultados (o `tfs` da URL diz o que buscar) e usa
o decodificador da swoop nos dados que vêm nela. O link da companhia sai do RPC
de reserva; se o Google recusar o RPC, o link é a tela de reserva do Google
Flights já com o voo escolhido, a um toque do site da companhia. As agências
ficam de fora: só aparecem para um navegador de verdade.
"""

from __future__ import annotations

import html
import json
import re
import secrets
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Literal, NotRequired, TypedDict

from mcp.server.mcpserver.exceptions import ToolError
from primp import Client
from swoop import Passengers, TransportConfig
from swoop import rpc as swoop_rpc
from swoop._selection import fetch_trip_booking_options
from swoop.decoder import Itinerary, decode_result
from swoop.exceptions import SwoopError

from . import tfs

Cabine = Literal["economica", "premium", "executiva", "primeira"]
Ordem = Literal["preco", "partida", "duracao"]

CABINES: dict[str, str] = {
    "economica": "economy",
    "premium": "premium-economy",
    "executiva": "business",
    "primeira": "first",
}
SITE = "https://www.google.com/travel/flights"
IATA = re.compile(r"^[A-Z]{3}$")
COMPANHIA = re.compile(r"^[A-Z0-9]{2}$")
REDIRECT = re.compile(r"url='([^']+)'", re.IGNORECASE)
Par = tuple[date, date | None]  # datas de ida e de volta
DIAS_MAX = 3  # dias antes e depois de cada data em precos_por_data
PARALELAS = 3  # buscas ao mesmo tempo em precos_por_data; 12 levam uns 4 s


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


class PrecoDoDia(TypedDict):
    data: str
    preco: int | None


class PrecosPorData(TypedDict):
    tipo: Literal["so_ida", "ida_e_volta"]
    moeda: str | None
    preco_quer_dizer: str
    ida: list[PrecoDoDia]
    volta: list[PrecoDoDia] | None


class Link(TypedDict):
    id: str
    onde: Literal["companhia", "google_flights"]
    companhia: str
    preco: int
    moeda: str | None
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

    def tipos(self) -> list[int]:
        return (
            [tfs.ADULTO] * self.adultos
            + [tfs.CRIANCA] * self.criancas
            + [tfs.BEBE_NO_COLO] * self.bebes_no_colo
        )


@dataclass
class Opcao:
    """Uma linha devolvida ao agente. `itinerarios` tem a ida e, no par, a volta."""

    pernas: list[tfs.Perna]
    itinerarios: list[Itinerary]
    filtros: Filtros
    precisa_volta: bool = False
    criada: float = field(default_factory=lambda: time.monotonic())

    @property
    def ida_e_volta(self) -> bool:
        return len(self.pernas) == 2


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
    def __init__(
        self,
        catalogo: Catalogo | None = None,
        pais: str = "BR",
        timeout_s: int = 30,
        pausa_rpc_s: float = 900,
    ):
        self.catalogo = catalogo or Catalogo()
        self.pais = pais
        self.pausa_rpc_s = pausa_rpc_s
        self.transporte = TransportConfig(timeout=timeout_s, retries=0, country=pais)
        self._http = Client(
            impersonate="chrome_145", impersonate_os="macos", cookie_store=True, timeout=timeout_s
        )
        # Recusado o RPC de reserva, não insiste por um tempo: insistir prolonga o bloqueio.
        self._rpc_volta_em = 0.0

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
        ida, volta = _datas(data, data_volta)
        _validar(filtros)
        pernas = _pernas(origem, destino, ida, volta, filtros)
        itinerarios = self._pagina(pernas, filtros)
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

    def precos_por_data(
        self,
        origem: str,
        destino: str,
        data: str,
        data_volta: str | None,
        filtros: Filtros,
        dias: int = DIAS_MAX,
    ) -> PrecosPorData:
        """O menor preço de cada dia perto das datas, para escolher quando viajar.

        Uma busca da página por dia, `PARALELAS` de cada vez. Na ida e volta, a fila da
        ida muda só a data da ida (a volta fica) e a da volta muda só a data da volta; o
        par pedido é buscado uma vez só. Dia que já passou, ou que poria a volta antes da
        ida, fica sem preço e nem vai ao Google. Não guarda ids: escolhida a data, o
        agente busca de novo com buscar_voos.
        """
        origem, destino = _iata(origem, "origem"), _iata(destino, "destino")
        ida, volta = _datas(data, data_volta)
        _validar(filtros)
        if not 1 <= dias <= DIAS_MAX:
            raise ErroDeBusca(f"dias vai de 1 a {DIAS_MAX}.")
        # Cada fila tem o dia que aparece e o par buscado para ele.
        perto = [timedelta(days=n) for n in range(-dias, dias + 1)]
        filas = {"ida": [(ida + d, (ida + d, volta)) for d in perto]}
        if volta:
            filas["volta"] = [(volta + d, (ida, volta + d)) for d in perto]
        hoje = date.today()
        pares = list(
            dict.fromkeys(
                par
                for fila in filas.values()
                for _, par in fila
                if par[0] >= hoje and (par[1] is None or par[1] >= par[0])
            )
        )

        def menor(par: Par) -> tuple[int | None, str | None, ErroDeBusca | None]:
            try:
                itinerarios = self._pagina(_pernas(origem, destino, *par, filtros), filtros)
            except ErroDeBusca as erro:
                return None, None, erro
            precos = [i.price for i in itinerarios if i.price]
            return (min(precos) if precos else None), _moeda(itinerarios), None

        with ThreadPoolExecutor(PARALELAS) as ex:
            achados = dict(zip(pares, ex.map(menor, pares), strict=True))
        erros = [e for _, _, e in achados.values() if e]
        if erros and len(erros) == len(achados):
            raise erros[0]

        def fila(nome: str) -> list[PrecoDoDia]:
            return [
                {"data": dia.isoformat(), "preco": achados[par][0] if par in achados else None}
                for dia, par in filas[nome]
            ]

        return {
            "tipo": "ida_e_volta" if volta else "so_ida",
            "moeda": next((m for _, m, _ in achados.values() if m), None),
            "preco_quer_dizer": (
                "menor total da ida e volta de cada dia; a outra data fica como pedida"
                if volta
                else "menor preço da ida de cada dia"
            ),
            "ida": fila("ida"),
            "volta": fila("volta") if volta else None,
        }

    def voltas(self, id_ida: str, ordenar: Ordem, limite: int) -> Voltas:
        opcao = self.catalogo.pegar(id_ida)
        if not opcao.precisa_volta:
            raise ErroDeBusca("Esse id não é de uma ida de ida e volta. Use link_de_compra(id).")
        ida = opcao.itinerarios[0]
        itinerarios = self._pagina(_escolher(opcao.pernas, [ida]), opcao.filtros)
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
        base: Link = {
            "id": ident,
            "onde": "google_flights",
            "companhia": ", ".join(_companhias(opcao.itinerarios)),
            "preco": opcao.itinerarios[-1].price,
            "moeda": _moeda(opcao.itinerarios),
            "link": self._reserva_no_google(opcao),
            "aviso": (
                "abre o Google Flights já com esse voo; é só tocar em reservar com a "
                "companhia. Preço do momento: a companhia confirma no checkout"
            ),
        }
        oferta = self._oferta_direta(opcao)
        if oferta is None:
            return base
        return base | {
            "onde": "companhia",
            "companhia": oferta.seller_name,
            "preco": oferta.price,
            "link": self._resolver(oferta.booking_url),
            "aviso": "preço do momento; a companhia confirma o valor final no checkout",
        }

    def _oferta_direta(self, opcao: Opcao):
        if time.monotonic() < self._rpc_volta_em:
            return None
        pernas = [swoop_rpc._normalize_rpc_leg(p.de, p.para, p.data) for p in opcao.pernas]
        try:
            ofertas = fetch_trip_booking_options(
                pernas,
                opcao.itinerarios,
                cabin=CABINES[opcao.filtros.cabine],
                passengers=opcao.filtros.passageiros(),
                transport=self.transporte,
            )
        except SwoopError:
            ofertas = []
        diretas = [o for o in ofertas if o.is_airline_direct and o.booking_url and o.price > 0]
        if not diretas:
            # Recusa e "sem oferta" chegam iguais; nos dois casos o link do Google serve.
            self._rpc_volta_em = time.monotonic() + self.pausa_rpc_s
            return None
        return min(diretas, key=lambda o: o.price)

    def _reserva_no_google(self, opcao: Opcao) -> str:
        pernas = _escolher(opcao.pernas, opcao.itinerarios)
        codigo = tfs.montar(
            pernas, opcao.filtros.tipos(), CABINES[opcao.filtros.cabine], opcao.ida_e_volta
        )
        return f"{SITE}/booking?tfs={codigo}&hl=pt-BR&curr=BRL"

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

    # Página de resultados

    def _pagina(self, pernas: list[tfs.Perna], filtros: Filtros) -> list[Itinerary]:
        codigo = tfs.montar(pernas, filtros.tipos(), CABINES[filtros.cabine], len(pernas) == 2)
        parametros = {"tfs": codigo, "hl": "pt-BR", "curr": "BRL", "gl": self.pais}
        try:
            resposta = self._http.get(f"{SITE}/search", params=parametros)
        except Exception as erro:
            raise ErroDeBusca("O Google Flights não respondeu. Tente de novo.") from erro
        if resposta.status_code == 429:
            raise ErroDeBusca("O Google limitou as consultas agora. Tente em alguns minutos.")
        if resposta.status_code != 200:
            raise ErroDeBusca(f"O Google Flights respondeu {resposta.status_code}.")
        return itinerarios_da_pagina(resposta.text)


def itinerarios_da_pagina(pagina: str) -> list[Itinerary]:
    """Os resultados vêm num `AF_initDataCallback` (ds:1) com o mesmo formato do RPC."""
    inicio = pagina.find("key: 'ds:1'")
    if inicio < 0:
        raise ErroDeBusca(
            "O Google Flights não devolveu a lista de voos. Pode ser limite temporário: "
            "tente em alguns minutos."
        )
    trecho = pagina[inicio : pagina.find("</script>", inicio)]
    try:
        dados = json.loads(trecho.split("data:", 1)[1].rsplit(", sideChannel", 1)[0])
        resultado = decode_result(dados) if dados else None
    except Exception as erro:
        raise ErroDeBusca("A página do Google Flights mudou e não deu para ler os voos.") from erro
    if resultado is None:
        return []
    return [i for i in (*resultado.best, *resultado.other) if i.price]


def _escolher(pernas: list[tfs.Perna], itinerarios: list[Itinerary]) -> list[tfs.Perna]:
    """As pernas com os voos já escolhidos, na ordem: a ida e, se houver, a volta."""
    escolhidas = list(pernas)
    for i, itin in enumerate(itinerarios):
        trechos = [
            tfs.Trecho(
                s.departure_airport_code,
                _dia(s.departure_date),
                s.arrival_airport_code,
                s.airline,
                s.flight_number,
            )
            for s in itin.segments
        ]
        escolhidas[i] = replace(escolhidas[i], escolhidos=trechos)
    return escolhidas


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


def _companhias(itinerarios: list[Itinerary]) -> list[str]:
    nomes: list[str] = []
    for itin in itinerarios:
        nomes += [n for n in itin.airline_names if n not in nomes]
    return nomes


def _dia(dia: tuple[int, int, int]) -> str:
    return f"{dia[0]:04d}-{dia[1]:02d}-{dia[2]:02d}"


def _quando(dia: tuple[int, int, int], hora: tuple[int, int]) -> str:
    return f"{_dia(dia)}T{hora[0]:02d}:{hora[1]:02d}"


def _moeda(itinerarios: list[Itinerary]) -> str | None:
    return next((i.currency for i in itinerarios if i.currency), None)


def _iata(valor: str, campo: str) -> str:
    codigo = valor.strip().upper()
    if not IATA.match(codigo):
        raise ErroDeBusca(f"{campo} precisa ser um código IATA de 3 letras, como GRU ou CNF.")
    return codigo


def _datas(data: str, data_volta: str | None) -> tuple[date, date | None]:
    ida = _data(data, "data")
    volta = _data(data_volta, "data_volta") if data_volta else None
    if volta and volta < ida:
        raise ErroDeBusca("A data_volta é anterior à data de ida.")
    return ida, volta


def _pernas(
    origem: str, destino: str, ida: date, volta: date | None, f: Filtros
) -> list[tfs.Perna]:
    """A ida, com o filtro de horário, e a volta, sem ele."""
    pernas = [
        tfs.Perna(
            ida.isoformat(),
            origem,
            destino,
            f.max_escalas,
            list(f.companhias),
            f.partida_apos,
            f.partida_ate,
        )
    ]
    if volta:
        pernas.append(
            tfs.Perna(volta.isoformat(), destino, origem, f.max_escalas, list(f.companhias))
        )
    return pernas


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
