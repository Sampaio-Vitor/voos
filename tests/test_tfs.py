from pathlib import Path

import pytest

from voos.google import ErroDeBusca, itinerarios_da_pagina
from voos.tfs import ADULTO, Perna, Trecho, montar

DADOS = Path(__file__).parent / "dados"


def test_busca_com_filtros_igual_a_url_aceita_pelo_google():
    # Gerada pelo fast-flights e conferida no site: Gol, sem escalas, saída às 22h.
    perna = Perna(
        "2026-11-20",
        "GRU",
        "REC",
        max_escalas=0,
        companhias=["G3"],
        partida_apos=22,
        partida_ate=22,
    )
    assert montar([perna], [ADULTO], "economy", False) == (
        "GiQSCjIwMjYtMTEtMjAoADICRzNAFkgWagUSA0dSVXIFEgNSRUNCAQFIAZgBAg=="
    )


def test_reserva_com_voo_escolhido_igual_a_url_aceita_pelo_google():
    # A tela "Opções de reserva" do G3 1560, aberta no site com este tfs.
    trecho = Trecho("GRU", "2026-11-20", "REC", "G3", "1560")
    perna = Perna("2026-11-20", "GRU", "REC", escolhidos=[trecho])
    assert montar([perna], [ADULTO], "economy", False) == (
        "GjwSCjIwMjYtMTEtMjAiIAoDR1JVEgoyMDI2LTExLTIwGgNSRUMqAkczMgQxNTYwagUSA0dSVXIFEgNSRUNCAQFIAZgBAg=="
    )


def test_le_os_voos_da_pagina_de_resultados():
    pagina = (DADOS / "ds1_gru_rec_gol.html").read_text()
    (gol,) = itinerarios_da_pagina(f"<html>{pagina}<script>outro</script></html>")
    assert (gol.price, gol.currency) == (1825, "BRL")
    assert [(s.airline, s.flight_number) for s in gol.segments] == [("G3", "1560")]
    assert gol.departure_time == (22, 5) and gol.booking_token


def test_pagina_sem_resultados_explica():
    with pytest.raises(ErroDeBusca, match="limite temporário"):
        itinerarios_da_pagina("<html>consent</html>")
