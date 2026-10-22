"""O parâmetro `tfs` das URLs do Google Flights: um protobuf em base64.

Só os campos usados aqui, conferidos contra o site. Um voo escolhido entra como
trecho selecionado da perna: assim a página já abre nas voltas daquela ida, ou
na tela de reserva do par.
"""

import base64
from collections.abc import Iterable
from dataclasses import dataclass, field

ASSENTOS = {"economy": 1, "premium-economy": 2, "business": 3, "first": 4}
ADULTO, CRIANCA, BEBE_NO_COLO = 1, 2, 4
IDA_E_VOLTA, SO_IDA = 1, 2


@dataclass(frozen=True)
class Trecho:
    """Um voo de uma perna já escolhida, como o Google identifica."""

    de: str
    data: str
    para: str
    companhia: str
    numero: str


@dataclass
class Perna:
    data: str
    de: str
    para: str
    max_escalas: int | None = None
    companhias: list[str] = field(default_factory=list)
    partida_apos: int | None = None
    partida_ate: int | None = None
    escolhidos: list[Trecho] = field(default_factory=list)


def montar(pernas: list[Perna], passageiros: Iterable[int], assento: str, ida_e_volta: bool) -> str:
    info = b"".join(_campo(3, _perna(p)) for p in pernas)
    info += _campo(8, b"".join(_varint(t) for t in passageiros))
    info += _numero(9, ASSENTOS[assento])
    info += _numero(19, IDA_E_VOLTA if ida_e_volta else SO_IDA)
    return base64.urlsafe_b64encode(info).decode()


def _perna(p: Perna) -> bytes:
    dados = _campo(2, p.data)
    for t in p.escolhidos:
        dados += _campo(
            4,
            _campo(1, t.de)
            + _campo(2, t.data)
            + _campo(3, t.para)
            + _campo(5, t.companhia)
            + _campo(6, t.numero),
        )
    if p.max_escalas is not None:
        dados += _numero(5, p.max_escalas)
    dados += b"".join(_campo(6, c) for c in p.companhias)
    if p.partida_apos is not None:
        dados += _numero(8, p.partida_apos)
    if p.partida_ate is not None:
        dados += _numero(9, p.partida_ate)
    dados += _campo(13, _campo(2, p.de)) + _campo(14, _campo(2, p.para))
    return dados


def _varint(n: int) -> bytes:
    saida = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        saida.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(saida)


def _numero(campo: int, valor: int) -> bytes:
    return _varint(campo << 3) + _varint(valor)


def _campo(campo: int, valor: str | bytes) -> bytes:
    dados = valor.encode() if isinstance(valor, str) else valor
    return _varint(campo << 3 | 2) + _varint(len(dados)) + dados
