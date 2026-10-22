"""MCP de voos: preços do Google Flights e link de compra na companhia.

Sem autenticação: o serviço só escuta no loopback e chega à tailnet pelo
`tailscale serve` (docs/deploy.md). Só lê dados públicos de preço.
"""

import argparse
import logging
import os
from typing import Annotated

import anyio
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .google import Busca, Cabine, Filtros, Google, Link, Ordem, Voltas

LOCAL = ("127.0.0.1:*", "localhost:*")
INSTRUCOES = """\
Preços de passagens aéreas do Google Flights, em reais, com link de compra no site da companhia.

Só ida: buscar_voos → link_de_compra(id).
Ida e volta: buscar_voos com data_volta → opcoes_de_volta(id da ida) → link_de_compra(id da volta).
Na ida e volta, o preço da ida é o total do par "a partir de";
o preço final é o da volta escolhida.
Aeroportos e companhias em códigos IATA (GRU, CGH, CNF, REC; G3 = Gol, AD = Azul, LA = LATAM).
Os ids valem por uma hora. Preço é do momento: a companhia confirma no checkout.
"""

Iata = Annotated[str, Field(description="Código IATA do aeroporto, 3 letras (ex.: CNF, GRU)")]
Data = Annotated[str, Field(description="Data no formato AAAA-MM-DD")]
Limite = Annotated[int, Field(ge=1, le=30, description="Quantos voos devolver")]
OrdemCampo = Annotated[Ordem, Field(description="preco, partida ou duracao")]


def criar(google: Google | None = None, hosts: tuple[str, ...] = LOCAL, revisao: str = "dev"):
    google = google or Google()
    mcp = MCPServer("Voos", instructions=INSTRUCOES)

    @mcp.tool()
    async def buscar_voos(
        origem: Iata,
        destino: Iata,
        data: Data,
        data_volta: Annotated[
            str | None, Field(description="AAAA-MM-DD para ida e volta; vazio é só ida")
        ] = None,
        adultos: Annotated[int, Field(ge=1, le=9)] = 1,
        criancas: Annotated[int, Field(ge=0, le=8, description="De 2 a 11 anos")] = 0,
        bebes_no_colo: Annotated[int, Field(ge=0, le=4, description="Menores de 2")] = 0,
        cabine: Cabine = "economica",
        max_escalas: Annotated[
            int | None, Field(ge=0, le=2, description="0 = só voos diretos; vazio = qualquer")
        ] = None,
        companhias: Annotated[
            list[str] | None,
            Field(description="Só estas companhias, em IATA (ex.: ['G3', 'AD'])"),
        ] = None,
        partida_apos: Annotated[
            int | None, Field(ge=0, le=23, description="Hora mínima de partida da ida")
        ] = None,
        partida_ate: Annotated[
            int | None, Field(ge=0, le=23, description="Hora máxima de partida da ida")
        ] = None,
        ordenar: OrdemCampo = "preco",
        limite: Limite = 8,
    ) -> Busca:
        """Busca voos com preço. Cada voo tem um id para link_de_compra ou opcoes_de_volta."""
        filtros = Filtros(
            adultos=adultos,
            criancas=criancas,
            bebes_no_colo=bebes_no_colo,
            cabine=cabine,
            max_escalas=max_escalas,
            companhias=tuple(c.strip().upper() for c in companhias or ()),
            partida_apos=partida_apos,
            partida_ate=partida_ate,
        )
        return await anyio.to_thread.run_sync(
            google.buscar, origem, destino, data, data_volta, filtros, ordenar, limite
        )

    @mcp.tool()
    async def opcoes_de_volta(
        id_ida: Annotated[str, Field(description="id de uma ida vinda de buscar_voos")],
        ordenar: OrdemCampo = "preco",
        limite: Limite = 8,
    ) -> Voltas:
        """Voltas para a ida escolhida, com o preço total do par."""
        return await anyio.to_thread.run_sync(google.voltas, id_ida, ordenar, limite)

    @mcp.tool()
    async def link_de_compra(
        id: Annotated[str, Field(description="id de um voo só de ida ou de uma volta")],
    ) -> Link:
        """Link para comprar no site da companhia, já com o voo escolhido, e o preço atual."""
        return await anyio.to_thread.run_sync(google.link, id)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "revisao": revisao})

    return mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=list(hosts)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("VOOS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VOOS_PORT", "8790")))
    args = parser.parse_args()
    # Host aceitos além do loopback (o SDK recusa o resto, contra DNS rebinding):
    # o nome da tailnet por onde o tailscale serve entrega as chamadas.
    extras = [h.strip() for h in os.environ.get("VOOS_HOSTS", "").split(",") if h.strip()]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = criar(hosts=(*LOCAL, *extras), revisao=os.environ.get("VOOS_REVISAO", "dev"))
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
