"""Résolution pure de la configuration héritée (sans E/S, testable seule)."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator

LEVELS = ("organization", "site", "group", "screen")
ALL_SCOPES = frozenset(LEVELS)

W_VALUE_INVALID = "config.warning.value_invalid"
W_SCOPE_NOT_ALLOWED = "config.warning.scope_not_allowed"
W_KEY_UNKNOWN = "config.warning.key_unknown"
W_ENV_INVALID = "config.warning.env_invalid"


@dataclass(frozen=True)
class ChainNode:
    level: str
    id: UUID
    name: str


@dataclass(frozen=True)
class StoredOverride:
    level: str
    scope_id: UUID
    key: str
    value: Any


@dataclass(frozen=True)
class Origin:
    level: str  # organization | site | group | screen | default | env
    id: UUID | None = None
    name: str | None = None


@dataclass(frozen=True)
class ResolvedKey:
    value: Any
    origin: Origin
    inherited: Any
    inherited_origin: Origin
    overridable: bool
    secret: bool = False
    is_set: bool = False
    # Valeur brute d'une clé secrète : réservée au module propriétaire, jamais sérialisée.
    secret_value: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class Warning_:
    code: str
    key: str
    level: str | None = None
    scope_id: UUID | None = None


@dataclass
class Resolution:
    values: dict[str, ResolvedKey] = field(default_factory=dict)
    warnings: list[Warning_] = field(default_factory=list)


def key_scopes(prop: Mapping[str, Any]) -> frozenset[str]:
    scopes = prop.get("x-vds-scopes")
    return frozenset(scopes) if scopes else ALL_SCOPES


def is_valid_value(schema: Mapping[str, Any], key: str, value: Any) -> bool:
    return not value_errors(schema, key, value)


def value_errors(schema: Mapping[str, Any], key: str, value: Any) -> list[str]:
    """Valide `value` contre le sous-schéma de la clé ; retourne les chemins en erreur."""
    prop = schema.get("properties", {}).get(key)
    if prop is None:
        return ["$"]
    sub = {
        "type": "object",
        "properties": {key: prop},
        "required": [key],
        "additionalProperties": False,
    }
    validator = Draft202012Validator(sub)
    return sorted(
        {
            "/".join(str(p) for p in e.absolute_path) or "$"
            for e in validator.iter_errors({key: value})
        }
    )


def _env_value(prop: Mapping[str, Any], raw: str) -> Any:
    """Variable d'environnement -> valeur typée selon le schéma (chaîne si le type est string)."""
    if prop.get("type") == "string":
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def resolve(
    schema: Mapping[str, Any],
    chain: Sequence[ChainNode],
    overrides: Sequence[StoredOverride],
    env: Mapping[str, str],
    target_level: str,
) -> Resolution:
    """Résout toutes les clés du schéma pour la cible (dernier noeud de `chain`).

    Précédence : défaut < organisation < site < groupe < écran ; une clé `x-vds-env` renseignée
    dans l'environnement l'emporte et n'est pas modifiable. `inherited` = valeur sans la
    surcharge du niveau `target_level`.
    """
    res = Resolution()
    props: Mapping[str, Any] = schema.get("properties", {})
    nodes = {n.level: n for n in chain}
    rank = {lvl: i for i, lvl in enumerate(LEVELS)}
    by_key: dict[str, list[StoredOverride]] = {}
    for o in sorted(overrides, key=lambda o: rank[o.level]):
        by_key.setdefault(o.key, []).append(o)

    for key in by_key.keys() - props.keys():
        for o in by_key[key]:
            res.warnings.append(Warning_(W_KEY_UNKNOWN, key, o.level, o.scope_id))

    for key, prop in props.items():
        scopes = key_scopes(prop)
        secret = prop.get("x-vds-secret") is True
        env_name = prop.get("x-vds-env")
        env_raw = env.get(env_name) if env_name else None
        value: Any = prop.get("default")
        origin = Origin("default")
        inh_value, inh_origin = value, origin
        # un secret ou une clé liée à l'env ne vient que de l'env (lignes héritées ignorées)
        for o in [] if secret or env_name else by_key.get(key, []):
            node = nodes.get(o.level)
            if node is None or node.id != o.scope_id:
                continue
            if o.level not in scopes:
                res.warnings.append(Warning_(W_SCOPE_NOT_ALLOWED, key, o.level, o.scope_id))
                continue
            if not is_valid_value(schema, key, o.value):
                res.warnings.append(Warning_(W_VALUE_INVALID, key, o.level, o.scope_id))
                continue
            value, origin = o.value, Origin(o.level, node.id, node.name)
            if rank[o.level] < rank[target_level]:
                inh_value, inh_origin = value, origin
        overridable = target_level in scopes
        if env_name and env_raw is not None:
            env_val = _env_value(prop, env_raw)
            if is_valid_value(schema, key, env_val):
                value, origin = env_val, Origin("env")
                inh_value, inh_origin = value, origin
                overridable = False
            else:
                res.warnings.append(Warning_(W_ENV_INVALID, key))
        elif env_name:
            overridable = False
        if secret:
            raw = value
            is_set = origin.level == "env" and raw not in (None, "")
            res.values[key] = ResolvedKey(
                None, origin, None, inh_origin, overridable, True, is_set, raw
            )
            continue
        res.values[key] = ResolvedKey(value, origin, inh_value, inh_origin, overridable, secret)
    return res
