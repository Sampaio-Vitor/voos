import pytest
from starlette.testclient import TestClient
from swoop.decoder import BookingOption

from voos.servidor import criar

from .conftest import itinerario

CABECALHOS = {"accept": "application/json, text/event-stream"}


@pytest.fixture
def cliente(google):
    with TestClient(criar(google, revisao="abc123"), base_url="http://127.0.0.1:8790") as c:
        yield c


def chamar(cliente, metodo, params=None, **kw):
    corpo = {"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params or {}}
    resposta = cliente.post("/mcp", json=corpo, headers=CABECALHOS, **kw)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["result"]


def ferramenta(cliente, nome, **argumentos):
    return chamar(cliente, "tools/call", {"name": nome, "arguments": argumentos})


def test_healthz(cliente):
    assert cliente.get("/healthz").json() == {"ok": True, "revisao": "abc123"}


def test_lista_as_tres_ferramentas(cliente):
    nomes = {t["name"] for t in chamar(cliente, "tools/list")["tools"]}
    assert nomes == {"buscar_voos", "opcoes_de_volta", "link_de_compra"}


def test_busca_e_link_pelo_mcp(cliente, falso):
    falso.resultados = [[itinerario([("G3", "1560", "GRU", "REC")], 1670)]]
    falso.ofertas = [
        BookingOption(
            price=1825, seller_name="Gol", is_airline_direct=True, booking_url="https://x"
        )
    ]
    r = ferramenta(
        cliente, "buscar_voos", origem="GRU", destino="REC", data="2030-11-20", companhias=["g3"]
    )
    assert not r["isError"]
    voo = r["structuredContent"]["voos"][0]
    assert voo["voos"] == ["G3 1560"] and voo["preco"] == 1670
    assert falso.buscas[0][0].companhias == ["G3"]

    link = ferramenta(cliente, "link_de_compra", id=voo["id"])["structuredContent"]
    assert (link["companhia"], link["preco"]) == ("Gol", 1825)


def test_erro_de_busca_chega_ao_agente(cliente):
    r = ferramenta(cliente, "link_de_compra", id="naoexiste")
    assert r["isError"]
    assert "Faça a busca de novo" in r["content"][0]["text"]


def test_argumento_fora_do_esquema(cliente):
    r = ferramenta(
        cliente, "buscar_voos", origem="GRU", destino="REC", data="2030-11-20", adultos=12
    )
    assert r["isError"]


def test_host_desconhecido_e_recusado(google):
    with TestClient(criar(google), base_url="http://evil.example") as c:
        corpo = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert c.post("/mcp", json=corpo, headers=CABECALHOS).status_code == 421
