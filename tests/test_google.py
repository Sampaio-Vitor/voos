import pytest
from swoop.decoder import BookingOption

from voos.google import Catalogo, ErroDeBusca, Filtros, Opcao
from voos.tfs import Trecho

from .conftest import itinerario

DIA = "2030-11-20"
VOLTA = "2030-11-27"
GOL = itinerario([("G3", "1560", "GRU", "REC")], 1670, partida=(22, 5))
AZUL = itinerario([("AD", "2959", "GRU", "REC")], 1032, partida=(14, 10))
LATAM = itinerario([("LA", "3555", "CNF", "GRU"), ("LA", "3376", "GRU", "REC")], 891)
PAGINA_GOL = (
    "<html><head><meta content=\"0;url='https://b2c.voegol.com.br/compra?de=GRU&amp;"
    'para=REC&amp;vooSelecionado1=G31560\'" http-equiv="refresh"></head></html>'
)


def oferta(vendedor, preco, direta=True, url="https://www.google.com/travel/clk/f?u=x"):
    return BookingOption(
        price=preco, seller_name=vendedor, is_airline_direct=direta, booking_url=url
    )


def buscar_um(google, falso, itin, volta=None):
    falso.resultados.append([itin])
    return google.buscar("GRU", "REC", DIA, volta, Filtros(), "preco", 5)["voos"][0]["id"]


def test_so_ida_ordena_por_preco_e_descreve_o_voo(google, falso):
    falso.resultados = [[GOL, AZUL, LATAM]]
    r = google.buscar("gru", "REC", DIA, None, Filtros(max_escalas=1), "preco", 2)
    assert r["tipo"] == "so_ida" and r["moeda"] == "BRL"
    assert [v["preco"] for v in r["voos"]] == [891, 1032]
    latam = r["voos"][0]
    assert latam["voos"] == ["LA 3555", "LA 3376"]
    assert latam["escalas"] == 1 and latam["conexoes"] == [{"aeroporto": "GRU", "espera_min": 60}]
    assert latam["partida"] == "2030-11-20T10:00"
    (perna,) = falso.buscas[0]
    assert (perna.de, perna.para, perna.max_escalas) == ("GRU", "REC", 1)


def test_filtro_de_horario_vale_so_para_a_ida(google, falso):
    falso.resultados = [[AZUL]]
    google.buscar("GRU", "REC", DIA, VOLTA, Filtros(partida_apos=6, partida_ate=12), "preco", 5)
    ida, volta = falso.buscas[0]
    assert (ida.partida_apos, ida.partida_ate) == (6, 12)
    assert (volta.de, volta.para, volta.data) == ("REC", "GRU", VOLTA)
    assert volta.partida_apos is None


def test_link_da_companhia_quando_o_google_deixa(google, falso):
    falso.ofertas = [oferta("123milhas", 1500, direta=False), oferta("Gol", 1825)]
    falso.pagina = PAGINA_GOL
    link = google.link(buscar_um(google, falso, GOL))
    assert (link["onde"], link["companhia"], link["preco"]) == ("companhia", "Gol", 1825)
    assert link["link"] == "https://b2c.voegol.com.br/compra?de=GRU&para=REC&vooSelecionado1=G31560"
    pernas, itinerarios = falso.reservas[0]
    assert itinerarios == [GOL] and pernas[0]["origin"] == "GRU"


def test_link_da_companhia_fica_no_google_se_a_pagina_mudar(google, falso):
    falso.ofertas = [oferta("Gol", 1825)]
    falso.pagina = "<html>outra coisa</html>"
    assert google.link(buscar_um(google, falso, GOL))["link"].startswith(
        "https://www.google.com/travel/clk/"
    )


def test_recusa_vira_tela_de_reserva_do_google_e_pausa_o_rpc(google, falso):
    falso.recusar_reserva = True
    link = google.link(buscar_um(google, falso, GOL))
    assert (link["onde"], link["companhia"], link["preco"]) == ("google_flights", "Gol", 1670)
    assert link["link"].startswith("https://www.google.com/travel/flights/booking?tfs=")
    assert "&curr=BRL" in link["link"]

    falso.recusar_reserva = False
    falso.ofertas = [oferta("Gol", 1825)]
    assert google.link(buscar_um(google, falso, GOL))["onde"] == "google_flights"
    assert len(falso.reservas) == 1  # pausado: nem tentou de novo


def test_ida_e_volta_em_duas_etapas(google, falso):
    volta = itinerario([("AD", "4820", "REC", "GRU")], 1477, dia=(2030, 11, 27))
    falso.resultados = [[AZUL], [volta]]
    falso.recusar_reserva = True
    r = google.buscar("GRU", "REC", DIA, VOLTA, Filtros(), "preco", 5)
    id_ida = r["voos"][0]["id"]
    assert r["tipo"] == "ida_e_volta" and "opcoes_de_volta" in r["proximo_passo"]

    with pytest.raises(ErroDeBusca, match="escolher a volta"):
        google.link(id_ida)

    v = google.voltas(id_ida, "preco", 5)
    assert v["ida"]["voos"] == ["AD 2959"] and "preco" not in v["ida"]
    ida_marcada, volta_livre = falso.buscas[1]
    assert ida_marcada.escolhidos == [Trecho("GRU", DIA, "REC", "AD", "2959")]
    assert volta_livre.escolhidos == [] and falso.buscas[0][0].escolhidos == []

    link = google.link(v["voos"][0]["id"])
    assert (link["preco"], link["companhia"]) == (1477, "Azul")
    _, itinerarios = falso.reservas[0]
    assert itinerarios == [AZUL, volta]


def test_voltas_recusa_id_de_so_ida(google, falso):
    with pytest.raises(ErroDeBusca, match="link_de_compra"):
        google.voltas(buscar_um(google, falso, GOL), "preco", 5)


@pytest.mark.parametrize(
    ("origem", "data", "volta", "filtros", "erro"),
    [
        ("SP", DIA, None, Filtros(), "IATA"),
        ("GRU", "20/11/2030", None, Filtros(), "AAAA-MM-DD"),
        ("GRU", "2020-01-01", None, Filtros(), "já passou"),
        ("GRU", VOLTA, DIA, Filtros(), "anterior"),
        ("GRU", DIA, None, Filtros(adultos=1, bebes_no_colo=2), "bebê"),
        ("GRU", DIA, None, Filtros(adultos=5, criancas=5), "9 passageiros"),
        ("GRU", DIA, None, Filtros(companhias=("GOL",)), "2 caracteres"),
    ],
)
def test_validacao(google, falso, origem, data, volta, filtros, erro):
    with pytest.raises(ErroDeBusca, match=erro):
        google.buscar(origem, "REC", data, volta, filtros, "preco", 5)
    assert falso.buscas == []


def test_catalogo_vence_e_tem_limite(monkeypatch):
    agora = [1000.0]
    monkeypatch.setattr("voos.google.time.monotonic", lambda: agora[0])
    catalogo = Catalogo(validade_s=60, maximo=2)
    a, b, c = (catalogo.guardar(Opcao([], [GOL], Filtros())) for _ in range(3))
    with pytest.raises(ErroDeBusca, match="não existe ou venceu"):
        catalogo.pegar(a)
    assert catalogo.pegar(b) and catalogo.pegar(c)
    agora[0] += 61
    with pytest.raises(ErroDeBusca):
        catalogo.pegar(c)
