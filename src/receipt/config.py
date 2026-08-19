"""Configuración de la aplicación, leída del entorno."""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from receipt.branding import APP_NAME


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False

    database_url: str
    test_database_url: str | None = None

    # Volcado de cada sentencia SQL. Aparte de `debug` a propósito: útil para
    # depurar una consulta concreta, insoportable el resto del tiempo.
    sql_echo: bool = False

    session_secret: str
    session_cookie_name: str = "receipt_session"
    session_max_age_days: int = 30

    azure_storage_connection_string: str
    blob_container: str = "receipts"

    # `console` escribe el correo al log, `memory` lo guarda para las pruebas y
    # `azure` lo manda de verdad por Communication Services.
    mail_backend: Literal["console", "memory", "azure"] = "console"

    # Solo lo usa `ConsoleMailer`, para que el volcado del log se lea bien. El
    # remitente real de Azure va en `acs_sender_address`, que es una dirección
    # pelada: el nombre visible se configura en el portal, en el dominio.
    mail_from: str = f"{APP_NAME} <no-responder@recibo.mx>"

    # Secretos de Communication Services. Vacíos salvo con `mail_backend=azure`,
    # donde el validador de abajo los exige.
    acs_connection_string: str = ""
    acs_sender_address: str = ""

    public_base_url: str = "http://localhost:8000"

    # Identidad del responsable en el aviso de privacidad (§10). El art. 15 de
    # la LFPDPPP exige nombre y domicilio, y un canal por el que se puedan
    # ejercer los derechos ARCO.
    #
    # Van en configuración y no en la plantilla por dos razones: el domicilio de
    # quien opera esto no tiene por qué vivir en el repositorio, y el correo de
    # contacto **tiene que recibir de verdad** — el remitente de Communication
    # Services es `DoNotReply` y solo envía, así que no sirve para esto.
    legal_responsable: str = ""
    legal_domicilio: str = ""
    legal_contacto_arco: str = ""

    # Intentos permitidos por ventana en login y recuperación de contraseña (§10).
    login_max_attempts: int = 8
    login_window_seconds: int = 300

    # §10 exige limitar `/r/{token}` para impedir enumeración por fuerza bruta.
    # El cupo es holgado a propósito: un cliente abre su recibo un par de veces
    # y a veces varios clientes comparten la IP de una red móvil. Con 32 bytes
    # de entropía en el token, el límite no es la única defensa — solo encarece
    # el barrido hasta volverlo inútil.
    public_receipt_max_requests: int = 60
    public_receipt_window_seconds: int = 60

    @model_validator(mode="after")
    def _check_mail_backend(self) -> "Settings":
        """Con `azure`, faltar un secreto impide arrancar.

        Es a propósito más duro que dejarlo pasar. Una aplicación que arranca
        creyendo que manda correo y no lo manda falla en silencio: el emisor ve
        «te enviamos la liga», el usuario no recibe nada y nadie se entera hasta
        que alguien se queja. Un contenedor que no levanta se nota en el
        despliegue, que es cuando toca notarlo.
        """
        if self.mail_backend == "azure":
            faltantes = [
                nombre
                for nombre, valor in (
                    ("ACS_CONNECTION_STRING", self.acs_connection_string),
                    ("ACS_SENDER_ADDRESS", self.acs_sender_address),
                )
                if not valor.strip()
            ]
            if faltantes:
                raise ValueError(f"MAIL_BACKEND=azure exige {' y '.join(faltantes)}.")
        return self

    @model_validator(mode="after")
    def _check_legal_identity(self) -> "Settings":
        """En producción, publicar el aviso sin responsable identificable no.

        El art. 15 de la LFPDPPP exige identidad, domicilio y un mecanismo para
        ejercer los derechos ARCO. Un aviso publicado sin ellos es defectuoso, y
        a diferencia de un contenedor que no levanta, **nadie lo nota**: la
        página se ve bien y el hueco solo aparece cuando alguien reclama.

        Fuera de producción se admiten vacíos: exigirlos rompería el desarrollo
        local y la suite sin proteger a nadie.
        """
        if self.environment == "production":
            faltantes = [
                nombre
                for nombre, valor in (
                    ("LEGAL_RESPONSABLE", self.legal_responsable),
                    ("LEGAL_DOMICILIO", self.legal_domicilio),
                    ("LEGAL_CONTACTO_ARCO", self.legal_contacto_arco),
                )
                if not valor.strip()
            ]
            if faltantes:
                raise ValueError(
                    f"El aviso de privacidad exige {' y '.join(faltantes)} en producción."
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cookies_require_https(self) -> bool:
        """`Secure` en la cookie de sesión (§3.1), salvo en local sobre HTTP."""
        return self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()
