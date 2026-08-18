"""La identidad del emisor en el documento, leída del snapshot congelado.

§8.1 imprime dos nombres distintos: el que encabeza el comprobante y el de quien
firma. Antes eran el mismo dato, así que los recibos emitidos hasta entonces
traen uno solo — y RN-10 impide reescribirlos. Lo que se prueba aquí es que el
render sabe leer las dos formas del snapshot.

Sin base de datos a propósito: `ReceiptDocument` es un dataclass y las
propiedades son lógica pura sobre un diccionario, así que el caso de la v1 se
puede fabricar tal cual y no hay que emitir un recibo para probarlo.
"""

from typing import Any

from receipt.models import Receipt
from receipt.services.rendering import ReceiptDocument


def a_document(issuer: dict[str, Any] | None) -> ReceiptDocument:
    return ReceiptDocument(
        receipt=Receipt(),
        snapshot={} if issuer is None else {"issuer": issuer},
        issued_on_text="",
        paid_on_text=None,
        cancelled_at_text=None,
        client_signed_at_text=None,
        logo_data_uri=None,
        issuer_signature_data_uri=None,
        client_signature_data_uri=None,
    )


class TestWhoHeadlinesAndWhoSigns:
    def test_the_business_headlines_and_the_person_signs(self) -> None:
        doc = a_document({"display_name": "Plomería Sánchez", "signer_name": "Ana Sánchez"})

        assert doc.issuer_name == "Plomería Sánchez"
        assert doc.signer_name == "Ana Sánchez"

    def test_without_a_business_both_places_show_the_person(self) -> None:
        doc = a_document({"display_name": "Ana Sánchez", "signer_name": "Ana Sánchez"})

        assert doc.issuer_name == doc.signer_name == "Ana Sánchez"


class TestVersionOneSnapshots:
    """Recibos emitidos antes de separar los dos nombres."""

    def test_it_signs_with_the_name_it_froze(self) -> None:
        """RN-10: no se puede reescribir, así que su firma tiene que salir de la
        única llave que tiene, que es la que ese recibo imprimía."""
        doc = a_document({"display_name": "Plomería Sánchez"})

        assert doc.signer_name == "Plomería Sánchez"

    def test_an_empty_signer_name_does_not_blank_the_line(self) -> None:
        """Una cadena vacía cae igual que una llave ausente: una línea de firma
        en blanco no la delata ni un error ni una prueba."""
        doc = a_document({"display_name": "Plomería Sánchez", "signer_name": ""})

        assert doc.signer_name == "Plomería Sánchez"


class TestAMalformedSnapshotDoesNotBreakTheDocument:
    """Un recibo es la prueba de pago de alguien: un 500 lo deja sin ella."""

    def test_a_snapshot_without_an_issuer_block(self) -> None:
        assert a_document(None).signer_name == ""

    def test_an_issuer_block_that_is_not_a_dict(self) -> None:
        doc = a_document(None)
        doc = ReceiptDocument(**{**doc.__dict__, "snapshot": {"issuer": "Juan Pérez"}})

        assert doc.issuer_name == ""
        assert doc.signer_name == ""

    def test_a_name_that_is_not_text(self) -> None:
        assert a_document({"display_name": 42}).issuer_name == ""
