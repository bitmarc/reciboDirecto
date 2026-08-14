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
    """Borra las variables de ACS del entorno del proceso.

    `_env_file=None` solo impide que pydantic lea el archivo `.env`; **no evita
    `os.environ`**, y Compose inyecta el contenido de `.env` como variables de
    entorno de verdad. Sin esto, quien ya tenga configurado el correo real ve
    fallar estas pruebas —el validador no salta porque el secreto sí está— y
    quien no, las ve pasar. Un verde que depende de la máquina no prueba nada.
    """
    monkeypatch.delenv("ACS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("ACS_SENDER_ADDRESS", raising=False)


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
