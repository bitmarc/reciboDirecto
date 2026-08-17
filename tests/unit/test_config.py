"""Validación de los ajustes que, mal puestos, fallan en silencio.

`MAIL_BACKEND=azure` sin secretos es el caso peligroso: la aplicación
arrancaría, el emisor leería «te enviamos la liga» y nadie recibiría nada. El
fallo no aparecería hasta que alguien se quejara, y entonces sin rastro. Es
preferible que el contenedor no levante.
"""

import pytest
from pydantic import ValidationError

from receipt.config import Settings

BASE = {
    "database_url": "postgresql+asyncpg://x:y@db:5432/z",
    "session_secret": "secreto",
    "azure_storage_connection_string": "UseDevelopmentStorage=true",
}


@pytest.fixture(autouse=True)
def sin_secretos_del_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """Borra del entorno del proceso lo que estas pruebas quieren ver faltar.

    `_env_file=None` solo impide que pydantic lea el archivo `.env`; **no evita
    `os.environ`**, y Compose inyecta el contenido de `.env` como variables de
    entorno de verdad. Sin esto, quien ya tenga configurado el correo real ve
    fallar estas pruebas —el validador no salta porque el secreto sí está— y
    quien no, las ve pasar. Un verde que depende de la máquina no prueba nada.

    Las de identidad legal las fija además el propio `conftest.py`, así que aquí
    hay que quitarlas explícitamente para poder probar que faltan.
    """
    for variable in (
        "ACS_CONNECTION_STRING",
        "ACS_SENDER_ADDRESS",
        "LEGAL_RESPONSABLE",
        "LEGAL_DOMICILIO",
        "LEGAL_CONTACTO_ARCO",
    ):
        monkeypatch.delenv(variable, raising=False)


def _settings(**overrides: str) -> Settings:
    # `_env_file=None` aísla del archivo; el fixture de arriba, del entorno.
    return Settings(**(BASE | overrides), _env_file=None)  # type: ignore[arg-type]


class TestElAdaptadorDeCorreo:
    def test_azure_without_a_connection_string_refuses_to_start(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _settings(mail_backend="azure", acs_sender_address="a@b.azurecomm.net")

        assert "ACS_CONNECTION_STRING" in str(exc.value)

    def test_azure_without_a_sender_refuses_to_start(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _settings(mail_backend="azure", acs_connection_string="endpoint=...;accesskey=...")

        assert "ACS_SENDER_ADDRESS" in str(exc.value)

    def test_the_error_names_both_when_both_are_missing(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _settings(mail_backend="azure")

        mensaje = str(exc.value)
        assert "ACS_CONNECTION_STRING" in mensaje
        assert "ACS_SENDER_ADDRESS" in mensaje

    def test_whitespace_does_not_count_as_a_value(self) -> None:
        """Una variable de entorno puesta a cadena vacía llega como `""`, y un
        copiado con salto de línea llega como `"\\n"`. Ninguno sirve."""
        with pytest.raises(ValidationError):
            _settings(
                mail_backend="azure",
                acs_connection_string="   ",
                acs_sender_address="a@b.azurecomm.net",
            )

    def test_azure_with_both_secrets_is_accepted(self) -> None:
        settings = _settings(
            mail_backend="azure",
            acs_connection_string="endpoint=https://x.communication.azure.com/;accesskey=k",
            acs_sender_address="DoNotReply@abc.azurecomm.net",
        )

        assert settings.mail_backend == "azure"

    def test_console_does_not_need_any_of_them(self) -> None:
        """El desarrollo en local no debe exigir credenciales de Azure."""
        assert _settings(mail_backend="console").acs_connection_string == ""

    def test_an_unknown_backend_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _settings(mail_backend="sendgrid")


LEGAL_COMPLETO = {
    "legal_responsable": "Recibo, S.A. de C.V.",
    "legal_domicilio": "Av. Siempre Viva 742, Ciudad de México",
    "legal_contacto_arco": "privacidad@recibo.mx",
}


class TestLaIdentidadDelResponsable:
    """El art. 15 de la LFPDPPP exige identidad, domicilio y canal ARCO.

    Un aviso publicado sin ellos es defectuoso, y a diferencia de un contenedor
    que no levanta, **nadie lo nota**: la página se ve bien y el hueco solo
    aparece cuando alguien reclama. Por eso en producción se prefiere no
    arrancar.
    """

    def test_production_without_the_responsable_refuses_to_start(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _settings(
                environment="production",
                **(LEGAL_COMPLETO | {"legal_responsable": ""}),
            )

        assert "LEGAL_RESPONSABLE" in str(exc.value)

    def test_production_without_an_address_refuses_to_start(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _settings(environment="production", **(LEGAL_COMPLETO | {"legal_domicilio": ""}))

        assert "LEGAL_DOMICILIO" in str(exc.value)

    def test_production_without_an_arco_contact_refuses_to_start(self) -> None:
        """Es el que más duele que falte: sin dirección de contacto, el aviso
        enumera derechos que nadie puede ejercer."""
        with pytest.raises(ValidationError) as exc:
            _settings(environment="production", **(LEGAL_COMPLETO | {"legal_contacto_arco": ""}))

        assert "LEGAL_CONTACTO_ARCO" in str(exc.value)

    def test_the_error_names_all_three_when_all_three_are_missing(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _settings(environment="production")

        mensaje = str(exc.value)
        assert "LEGAL_RESPONSABLE" in mensaje
        assert "LEGAL_DOMICILIO" in mensaje
        assert "LEGAL_CONTACTO_ARCO" in mensaje

    def test_production_with_the_three_is_accepted(self) -> None:
        assert _settings(environment="production", **LEGAL_COMPLETO).is_production

    def test_development_does_not_demand_them(self) -> None:
        """Exigirlas fuera de producción rompería el desarrollo local y la suite
        sin proteger a nadie: ahí el aviso no lo lee ningún titular."""
        assert _settings().legal_responsable == ""
