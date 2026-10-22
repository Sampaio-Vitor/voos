from dataclasses import dataclass, field

import pytest
from swoop.builders import ItinerarySummary
from swoop.decoder import BookingOption, Itinerary, Layover, Segment

from voos import google as modulo
from voos.google import Catalogo, Google


def itinerario(
    voos: list[tuple[str, str, str, str]],
    preco: int,
    partida: tuple[int, int] = (10, 0),
    dia: tuple[int, int, int] = (2030, 11, 20),
    minutos: int = 180,
) -> Itinerary:
    """`voos` são trechos (companhia, número, de, para)."""
    segmentos = [
        Segment(
            airline=cia,
            airline_name={"G3": "Gol", "AD": "Azul", "LA": "LATAM"}.get(cia, cia),
            flight_number=numero,
            departure_airport_code=de,
            arrival_airport_code=para,
            departure_date=dia,
            arrival_date=dia,
        )
        for cia, numero, de, para in voos
    ]
    conexoes = [
        Layover(minutes=60, departure_airport_code=s.arrival_airport_code) for s in segmentos[:-1]
    ]
    return Itinerary(
        airline_code=segmentos[0].airline,
        airline_names=sorted({s.airline_name for s in segmentos}),
        segments=segmentos,
        layovers=conexoes,
        travel_time=minutos,
        departure_airport_code=segmentos[0].departure_airport_code,
        arrival_airport_code=segmentos[-1].arrival_airport_code,
        departure_date=dia,
        arrival_date=dia,
        departure_time=partida,
        arrival_time=(partida[0] + 3, partida[1]),
        price_info=ItinerarySummary("", 0, "BRL"),
        direct_price=preco,
        booking_token=f"tok-{voos[0][1]}",
        stop_count=len(segmentos) - 1,
    )


@dataclass
class Resposta:
    text: str = ""
    headers: dict = field(default_factory=dict)
    status_code: int = 200


@dataclass
class GoogleFalso:
    """Faz o papel do Google: guarda as consultas e devolve o que o teste mandar."""

    buscas: list = field(default_factory=list)
    resultados: list[list[Itinerary]] = field(default_factory=list)
    reservas: list = field(default_factory=list)
    ofertas: list[BookingOption] = field(default_factory=list)
    recusar_reserva: bool = False
    pagina: str = ""

    def buscar(self, pernas, filtros):
        self.buscas.append(pernas)
        return self.resultados.pop(0)

    def reservar(self, pernas, itinerarios, **_):
        self.reservas.append((pernas, itinerarios))
        if self.recusar_reserva:
            return []
        return self.ofertas

    def get(self, url):
        return Resposta(text=self.pagina)


@pytest.fixture
def falso(monkeypatch):
    g = GoogleFalso()
    monkeypatch.setattr(modulo, "fetch_trip_booking_options", g.reservar)
    return g


@pytest.fixture
def google(falso):
    servico = Google(Catalogo())
    servico._http = falso
    servico._pagina = falso.buscar
    return servico
