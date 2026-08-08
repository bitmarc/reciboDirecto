"""Almacenamiento de blobs — Azure Blob Storage, Azurite en local.

El contenedor es **privado** (§3.3). Ningún blob se expone por URL directa:
todo acceso pasa por la aplicación, porque el PNG de una firma es dato personal
(§10).

**Los blobs se versionan y nunca se sobrescriben.** No es una preferencia de
estilo. `receipt.snapshot` guarda *rutas*, no bytes; si al recapturar la firma
se pisara el blob anterior, todos los recibos ya emitidos cambiarían de firma
sin que se tocara una fila de la base de datos, y `AC-02` fallaría por una vía
que ninguna prueba de base de datos detecta. Los blobs huérfanos son correctos
y esperados, igual que los huecos de folio de RN-23.
"""

import contextlib
import uuid
from typing import Literal

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient

from receipt.config import get_settings

MediaKind = Literal["signature", "logo", "receipt-pdf"]

# Versión del protocolo de Azure Storage que se fija explícitamente.
#
# El SDK usa por omisión la más reciente que conoce, y Azurite va por detrás: sin
# fijarla, cada actualización del SDK rompe el entorno local con un
# `InvalidHeaderValue` que no dice nada útil. Fijarla también evita que dev y
# producción hablen protocolos distintos. Azure admite versiones antiguas
# indefinidamente, así que subirla es una decisión, no una urgencia.
API_VERSION = "2025-11-05"


class BlobNotFound(LookupError):
    """La ruta no existe en el contenedor."""


def new_path(kind: MediaKind, account_id: int, extension: str = "png") -> str:
    """Ruta nueva e irrepetible para una cuenta.

    El UUID es lo que garantiza que dos capturas nunca colisionen, y con él la
    subida sin sobrescritura deja de depender de que nadie se equivoque.
    """
    return f"accounts/{account_id}/{kind}/{uuid.uuid4()}.{extension}"


class BlobStore:
    """Cliente del contenedor de la aplicación.

    El contenedor se crea de forma perezosa, no al arrancar: levantar la app no
    debe depender de que Azurite ya esté vivo, y en producción el contenedor ya
    existe y la comprobación se salta tras la primera llamada.
    """

    def __init__(self, connection_string: str, container: str) -> None:
        self._connection_string = connection_string
        self._container = container
        self._container_ready = False

    def _service(self) -> BlobServiceClient:
        return BlobServiceClient.from_connection_string(
            self._connection_string, api_version=API_VERSION
        )

    async def _ensure_container(self, service: BlobServiceClient) -> None:
        if self._container_ready:
            return
        # Sin `public_access`: el contenedor nace privado y se queda privado.
        with contextlib.suppress(ResourceExistsError):
            await service.create_container(self._container)
        self._container_ready = True

    async def upload(self, path: str, data: bytes, *, content_type: str = "image/png") -> str:
        """Sube `data` a `path` y devuelve la ruta.

        `overwrite=False` es la segunda mitad de la garantía de versionado: si
        una ruta se reutilizara por error, la subida falla en lugar de destruir
        el original de forma silenciosa.
        """
        async with self._service() as service:
            await self._ensure_container(service)
            blob = service.get_blob_client(self._container, path)
            await blob.upload_blob(
                data,
                overwrite=False,
                content_settings=ContentSettings(content_type=content_type),
            )
        return path

    async def download(self, path: str) -> bytes:
        async with self._service() as service:
            blob = service.get_blob_client(self._container, path)
            try:
                stream = await blob.download_blob()
                return await stream.readall()
            except ResourceNotFoundError as exc:
                raise BlobNotFound(path) from exc

    async def exists(self, path: str) -> bool:
        async with self._service() as service:
            blob = service.get_blob_client(self._container, path)
            return await blob.exists()


_store: BlobStore | None = None


def get_blob_store() -> BlobStore:
    global _store
    if _store is None:
        settings = get_settings()
        _store = BlobStore(settings.azure_storage_connection_string, settings.blob_container)
    return _store


def reset_blob_store() -> None:
    """Descarta el cliente cacheado. Lo usan las pruebas al cambiar de config."""
    global _store
    _store = None
