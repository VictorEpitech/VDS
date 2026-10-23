"""Erreurs du contrat de module. Les messages sont des codes stables, jamais du texte UI."""


class ContractError(Exception):
    """Violation du contrat de module ; `code` est un code stable traduit par l'admin."""

    code: str = "contract.invalid"

    def __init__(self, code: str | None = None, detail: str = "") -> None:
        self.code = code or type(self).code
        self.detail = detail
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


class IncompatibleCore(ContractError):
    code = "module.core_incompatible"


class ConfigSchemaError(ContractError):
    code = "module.config_schema_invalid"


class UndeclaredEvent(ContractError):
    code = "module.event_undeclared"


class PermissionDenied(ContractError):
    code = "module.permission_denied"


class AccessDenied(ContractError):
    """Levée par un auth-provider pour refuser l'accès (ex. financeur)."""

    code = "auth.access_denied"
