"""El aviso de privacidad no puede volverse falso en silencio.

El resto del texto legal envejece de formas que se notan al leerlo. El
inventario de datos no: el día que alguien añada una columna con un dato
personal nuevo, la página seguirá diciendo exactamente lo mismo y habrá dejado
de ser cierta. Nadie lo descubre leyendo, porque nadie relee un aviso de
privacidad.

Estas pruebas recorren el **esquema real** y obligan a clasificar cada columna
y cada tabla nueva. Cuando fallen, lo que hay que hacer no es silenciarlas: es
decidir si el dato nuevo es personal y, si lo es, decirlo en el aviso.
"""

import receipt.models  # noqa: F401  — registra todos los modelos en el metadata
from receipt.models.base import Base
from receipt.web.legal import (
    INVENTARIO,
    NO_PERSONALES,
    TABLAS_CON_DATOS_PERSONALES,
    TABLAS_OPERATIVAS,
)

# Andamiaje del esquema, no contenido: claves, relaciones y marcas de tiempo de
# fila. No describen a nadie y clasificarlas una por una solo añadiría ruido.
ESTRUCTURALES = {"id", "created_at", "updated_at"}


def _columnas_revisables(nombre_tabla: str) -> set[str]:
    tabla = Base.metadata.tables[nombre_tabla]
    return {
        f"{nombre_tabla}.{columna.name}"
        for columna in tabla.columns
        if columna.name not in ESTRUCTURALES
        and not columna.primary_key
        and not columna.foreign_keys
    }


def _declaradas() -> set[str]:
    return {columna for categoria in INVENTARIO for columna in categoria.columnas}


class TestElInventarioCubreElEsquema:
    def test_every_column_of_a_personal_data_table_is_classified(self) -> None:
        """Una columna nueva sin clasificar rompe esto, y debe romperlo.

        El mensaje dice la columna exacta para que quien la agregó sepa qué
        decidir: o entra al inventario de `web/legal.py` y se explica en el
        aviso, o se declara en `NO_PERSONALES` con su motivo.
        """
        declaradas = _declaradas() | NO_PERSONALES

        sin_clasificar = {
            columna
            for tabla in TABLAS_CON_DATOS_PERSONALES
            for columna in _columnas_revisables(tabla)
            if columna not in declaradas
        }

        assert not sin_clasificar, (
            f"Columnas sin clasificar: {sorted(sin_clasificar)}. "
            "Decide si son datos personales: al inventario de web/legal.py y al "
            "aviso, o a NO_PERSONALES explicando por qué no lo son."
        )

    def test_the_inventory_does_not_name_columns_that_no_longer_exist(self) -> None:
        """Al revés: una columna borrada deja el aviso prometiendo de más."""
        reales = {
            columna
            for tabla in TABLAS_CON_DATOS_PERSONALES
            for columna in _columnas_revisables(tabla)
        }

        fantasmas = (_declaradas() | NO_PERSONALES) - reales

        assert not fantasmas, (
            f"El inventario nombra columnas que ya no existen: {sorted(fantasmas)}."
        )

    def test_every_table_is_classified(self) -> None:
        """Una tabla nueva entera también tiene que decidirse.

        Sin esto, alguien podría añadir `client_note` con texto libre sobre una
        persona y las pruebas de arriba ni se enterarían: solo miran las tablas
        que ya estaban en la lista.
        """
        clasificadas = TABLAS_CON_DATOS_PERSONALES | TABLAS_OPERATIVAS
        reales = set(Base.metadata.tables)

        assert reales == clasificadas, (
            f"Tablas sin clasificar: {sorted(reales - clasificadas)}. "
            f"Tablas declaradas que ya no existen: {sorted(clasificadas - reales)}."
        )


class TestElInventarioSeLeeBien:
    def test_no_category_is_empty(self) -> None:
        """Una categoría sin datos visibles sería un encabezado hueco."""
        for categoria in INVENTARIO:
            assert categoria.datos, f"«{categoria.titulo}» no enumera ningún dato"
            assert categoria.columnas, f"«{categoria.titulo}» no se ata a ninguna columna"

    def test_every_category_says_what_it_is_for(self) -> None:
        """El art. 15 exige las finalidades, y una por categoría es lo que las
        hace comprensibles: «para qué quieren mi teléfono» se responde al lado
        del teléfono, no en una lista aparte."""
        for categoria in INVENTARIO:
            assert categoria.para_que.strip(), f"«{categoria.titulo}» no dice para qué"
