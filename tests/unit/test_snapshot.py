"""El contenido congelado del recibo (§4.3), sin base de datos.

Lo que más importa aquí no es lo que el snapshot contiene, sino **lo que no
contiene**: el material es registro interno (RN-14) y el snapshot es
exactamente lo que se le entrega al cliente.
"""

import json
from datetime import datetime

from receipt.domain.clock import LOCAL_TZ
from receipt.domain.enums import PaymentMethod
from receipt.domain.snapshot import (
    AdjustmentFacts,
    ClientFacts,
    IssuerFacts,
    ServiceFacts,
    build_snapshot,
    concept_line,
)

ISSUER = IssuerFacts(
    display_name="Juan Pérez",
    phone="5512345678",
    occupation="Electricista",
    logo_blob_path="logos/1/v1.png",
    signature_blob_path="firmas/1/v1.png",
)
CUSTOMER = ClientFacts(full_name="Ana Ramírez", phone="5598765432")
MOMENT = datetime(2026, 7, 7, 13, 42, tzinfo=LOCAL_TZ)


def snapshot_of(
    *,
    agreed_total_cents: int | None = 230000,
    adjustments: list[AdjustmentFacts] | None = None,
    previous_paid_cents: int = 180000,
    payment_cents: int = 60000,
    material_cents: int = 0,
    include_logo: bool = True,
) -> dict:
    return build_snapshot(
        issued_at=MOMENT,
        paid_at=MOMENT,
        city="Ciudad de México",
        issuer=ISSUER,
        client=CUSTOMER,
        service=ServiceFacts(
            code="SRV-0001",
            concept="Instalación de contactos",
            agreed_total_cents=agreed_total_cents,
            location_alias="Casa Ramírez",
        ),
        adjustments=adjustments or [],
        previous_paid_cents=previous_paid_cents,
        payment_cents=payment_cents,
        material_cents=material_cents,
        method=PaymentMethod.CASH,
        include_logo=include_logo,
    )


class TestTheExampleOfSection43:
    """El caso literal del PRD: $2,300 acordados, $100 de extra, $600 de pago."""

    def test_amounts_match_the_documented_example(self) -> None:
        amounts = snapshot_of(
            adjustments=[
                AdjustmentFacts(amount_cents=10000, reason="Cambio de 2 contactos en recámara")
            ]
        )["amounts"]

        assert amounts["agreed_total_cents"] == 230000
        assert amounts["current_total_cents"] == 240000
        assert amounts["previous_paid_cents"] == 180000
        assert amounts["payment_cents"] == 60000
        assert amounts["accumulated_paid_cents"] == 240000
        assert amounts["balance_cents"] == 0

    def test_rn12_the_reason_of_each_adjustment_travels_verbatim(self) -> None:
        amounts = snapshot_of(
            adjustments=[AdjustmentFacts(amount_cents=10000, reason="Cambio de 2 contactos")]
        )["amounts"]

        assert amounts["adjustments"] == [
            {"amount_cents": 10000, "reason": "Cambio de 2 contactos"}
        ]

    def test_the_local_time_is_frozen_in_mexico_city(self) -> None:
        """§4.3 guarda la hora local, no UTC: es la que se imprime en el recibo."""
        assert snapshot_of()["issued_at_local"].startswith("2026-07-07T13:42:00")


def every_value(node: object) -> list:
    """Todos los valores hoja del snapshot, a cualquier profundidad."""
    if isinstance(node, dict):
        return [leaf for value in node.values() for leaf in every_value(value)]
    if isinstance(node, list):
        return [leaf for item in node for leaf in every_value(item)]
    return [node]


class TestRn14MaterialNeverReachesTheReceipt:
    def test_ac06_the_material_amount_is_absent_from_the_whole_snapshot(self) -> None:
        """Un pago de $1,000 con $300 de material se congela como $1,000.

        Se recorren los valores en lugar de buscar la cifra en el JSON: "30000"
        es una subcadena de "230000" y esa prueba fallaría por un total acordado
        que no tiene nada que ver con el material.
        """
        snapshot = snapshot_of(payment_cents=100000, material_cents=30000)

        assert snapshot["amounts"]["payment_cents"] == 100000
        assert "material_cents" not in snapshot["amounts"]
        assert 30000 not in every_value(snapshot)
        assert 70000 not in every_value(snapshot), "tampoco el ingreso neto de RN-14"

    def test_section83_the_concept_is_worded_as_an_aggregate(self) -> None:
        snapshot = snapshot_of(payment_cents=100000, material_cents=30000)
        assert snapshot["service"]["concept_line"] == (
            "Instalación de contactos — mano de obra y material"
        )

    def test_without_material_the_concept_is_untouched(self) -> None:
        assert snapshot_of()["service"]["concept_line"] == "Instalación de contactos"

    def test_concept_line_is_reusable_on_its_own(self) -> None:
        assert concept_line("Reparación", includes_material=False) == "Reparación"
        assert concept_line("Reparación", includes_material=True) == (
            "Reparación — mano de obra y material"
        )


class TestRn34Logo:
    def test_the_logo_travels_when_the_plan_allows_it(self) -> None:
        assert snapshot_of(include_logo=True)["issuer"]["logo_blob_path"] == "logos/1/v1.png"

    def test_a_free_account_freezes_the_receipt_without_a_logo(self) -> None:
        """Los recibos ya emitidos conservan el suyo; los nuevos no lo llevan."""
        assert snapshot_of(include_logo=False)["issuer"]["logo_blob_path"] is None

    def test_the_signature_of_the_issuer_is_not_a_paid_feature(self) -> None:
        """RN-20: se estampa en todos los recibos, sin importar el plan."""
        assert snapshot_of(include_logo=False)["issuer"]["signature_blob_path"] is not None


class TestPaymentsWithoutAnAgreedTotal:
    def test_rn15_a_single_payment_has_no_total_and_no_balance(self) -> None:
        amounts = snapshot_of(agreed_total_cents=None, previous_paid_cents=0, payment_cents=60000)[
            "amounts"
        ]

        assert amounts["agreed_total_cents"] is None
        assert amounts["current_total_cents"] is None
        assert amounts["balance_cents"] is None
        assert amounts["accumulated_paid_cents"] == 60000


class TestSnapshotIsSelfSufficient:
    def test_it_survives_a_round_trip_through_json(self) -> None:
        """Se guarda en `jsonb`: nada del snapshot puede depender de objetos vivos."""
        snapshot = snapshot_of(adjustments=[AdjustmentFacts(amount_cents=10000, reason="Extra")])
        assert json.loads(json.dumps(snapshot, ensure_ascii=False)) == snapshot

    def test_it_carries_a_format_version(self) -> None:
        """RN-10 impide reescribir recibos, así que el formato tiene que decir
        qué forma tiene para poder evolucionar sin adivinar."""
        assert snapshot_of()["version"] == 1
