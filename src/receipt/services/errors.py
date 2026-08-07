"""Errores de dominio que la capa web traduce a respuestas.

Se separan de `HTTPException` para que la capa de servicios no dependa de FastAPI
y se pueda probar sin cliente HTTP.
"""


class DomainError(Exception):
    """Error atribuible a la entrada del usuario, no a un fallo del sistema."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(DomainError):
    """El dato no cumple una regla de captura."""


class ConflictError(DomainError):
    """La operación choca con el estado actual (correo ya registrado, etc.)."""


class BusinessRuleViolation(DomainError):
    """Una regla de negocio numerada del PRD impide la operación.

    Lleva la regla en el mensaje para que el reporte al responsable de producto
    sea inequívoco (§0: si una decisión de implementación entra en conflicto con
    una RN, gana la RN y hay que reportarlo).
    """

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule
