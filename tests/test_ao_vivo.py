"""Consulta o Google Flights de verdade: `uv run pytest -m ao_vivo`.

Fica fora do CI comum porque depende do Google; serve para saber cedo se ele ou a
swoop mudaram (o CI roda toda semana com este marcador).
"""

from datetime import date, timedelta

import pytest

from voos.google import Filtros, Google

pytestmark = pytest.mark.ao_vivo


@pytest.fixture(scope="module")
def google():
    return Google()


def test_so_ida_com_link_da_companhia(google):
    dia = (date.today() + timedelta(days=45)).isoformat()
    r = google.buscar("GRU", "REC", dia, None, Filtros(max_escalas=0), "preco", 5)
    assert r["moeda"] == "BRL" and r["voos"]
    link = google.link(r["voos"][0]["id"])
    assert link["preco"] > 0 and link["link"].startswith("https://")
    # Com o RPC de reserva liberado, o link é o da companhia; recusado, o do Google.
    assert ("google.com" in link["link"]) == (link["onde"] == "google_flights")


def test_ida_e_volta(google):
    ida = date.today() + timedelta(days=45)
    r = google.buscar(
        "GRU",
        "REC",
        ida.isoformat(),
        (ida + timedelta(days=7)).isoformat(),
        Filtros(max_escalas=0),
        "preco",
        3,
    )
    voltas = google.voltas(r["voos"][0]["id"], "preco", 3)
    assert voltas["voos"]
    link = google.link(voltas["voos"][0]["id"])
    assert link["preco"] > 0 and link["link"].startswith("https://")


def test_precos_por_data(google):
    ida = date.today() + timedelta(days=45)
    r = google.precos_por_data(
        "GRU", "REC", ida.isoformat(), (ida + timedelta(days=7)).isoformat(), Filtros(), 1
    )
    assert r["moeda"] == "BRL" and len(r["ida"]) == len(r["volta"]) == 3
    assert any(d["preco"] for d in r["ida"])
