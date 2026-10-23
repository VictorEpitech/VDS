"""Validation du `config.schema.json` d'un module (extensions `x-vds-*`)."""

from typing import Any

from .errors import ConfigSchemaError

LEVELS = frozenset({"simple", "advanced"})
SCOPES = frozenset({"organization", "site", "group", "screen"})


def _fail(prop: str, code: str) -> ConfigSchemaError:
    return ConfigSchemaError(f"module.config_schema.{code}", prop)


def validate_config_schema(schema: dict[str, Any]) -> None:
    """Lève ConfigSchemaError (code stable) si le schéma viole le contrat 1.0."""
    if schema.get("type") != "object":
        raise _fail("$", "type_not_object")
    if schema.get("additionalProperties") is not False:
        raise _fail("$", "additional_properties_not_false")
    props = schema.get("properties")
    if not isinstance(props, dict):
        raise _fail("$", "properties_missing")
    required = set(schema.get("required", []))
    for name, p in props.items():
        if not isinstance(p, dict):
            raise _fail(name, "property_invalid")
        for key in ("title", "description"):
            if not isinstance(p.get(key), str) or not p[key]:
                raise _fail(name, f"{key}_missing")
        if "default" not in p and name not in required:
            raise _fail(name, "default_missing")
        if "x-vds-level" not in p:
            raise _fail(name, "level_missing")
        if p["x-vds-level"] not in LEVELS:
            raise _fail(name, "level_invalid")
        if "x-vds-scopes" in p:
            scopes = p["x-vds-scopes"]
            if not isinstance(scopes, list) or not scopes or any(s not in SCOPES for s in scopes):
                raise _fail(name, "scopes_invalid")
        env = p.get("x-vds-env")
        if env is not None and (not isinstance(env, str) or not env):
            raise _fail(name, "env_invalid")
        secret = p.get("x-vds-secret")
        if secret is not None and not isinstance(secret, bool):
            raise _fail(name, "secret_invalid")
        if secret is True and not env:
            raise _fail(name, "secret_requires_env")
    for group in schema.get("x-vds-groups", []):
        for field in group.get("fields", []):
            if field not in props:
                raise _fail(str(field), "group_field_unknown")


def secret_keys(schema: dict[str, Any]) -> frozenset[str]:
    """Clés à ne jamais renvoyer ni journaliser."""
    return frozenset(
        k for k, p in schema.get("properties", {}).items() if p.get("x-vds-secret") is True
    )


def env_keys(schema: dict[str, Any]) -> dict[str, str]:
    """Clé -> variable d'environnement (clés non modifiables par l'API)."""
    return {k: p["x-vds-env"] for k, p in schema.get("properties", {}).items() if "x-vds-env" in p}
