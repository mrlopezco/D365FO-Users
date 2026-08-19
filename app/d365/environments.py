"""Load D365 F&O environment definitions from YAML (including client secrets)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.d365.fo_client import normalize_environment_url

DEFAULT_ENVIRONMENTS_FILENAME = "d365_environments.yaml"
EXAMPLE_ENVIRONMENTS_FILENAME = "d365_environments.example.yaml"


@dataclass(frozen=True)
class D365Environment:
    name: str
    environment_url: str
    tenant_id: str
    client_id: str
    client_secret: str
    company: str | None = None


def _parse_environment(
    raw: dict[str, Any],
    source: str,
    *,
    require_secrets: bool,
) -> D365Environment:
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError(f"Environment entry missing name in {source}")

    environment_url = str(raw.get("environment_url", "")).strip()
    tenant_id = str(raw.get("tenant_id", "")).strip()
    client_id = str(raw.get("client_id", "")).strip()
    client_secret = str(raw.get("client_secret", "")).strip()

    missing = [
        label
        for label, val in (
            ("environment_url", environment_url),
            ("tenant_id", tenant_id),
            ("client_id", client_id),
        )
        if not val
    ]
    if require_secrets and not client_secret:
        missing.append("client_secret")
    if missing:
        raise ValueError(
            f"Environment '{name}' in {source} is missing: {', '.join(missing)}"
        )

    company_raw = raw.get("company")
    company = str(company_raw).strip() if company_raw not in (None, "") else None

    return D365Environment(
        name=name,
        environment_url=normalize_environment_url(environment_url),
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        company=company,
    )


def environments_config_hint(config_path: Path) -> str:
    example = config_path.parent / EXAMPLE_ENVIRONMENTS_FILENAME
    if example.is_file():
        return (
            f"Copy {example.name} to {config_path.name} and fill in client_secret "
            f"for each environment."
        )
    return f"Create {config_path.name} with your D365 connection settings."


def load_environments(
    config_path: Path,
    *,
    require_secrets: bool = True,
) -> list[D365Environment]:
    if not config_path.is_file():
        hint = environments_config_hint(config_path)
        raise FileNotFoundError(
            f"D365 environments config not found: {config_path}\n{hint}"
        )

    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid environments config (expected mapping): {config_path}")

    entries = data.get("environments")
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            f"No environments defined in {config_path} (expected non-empty 'environments' list)"
        )

    seen: set[str] = set()
    result: list[D365Environment] = []
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError(f"Each environment entry must be a mapping in {config_path}")
        env = _parse_environment(item, str(config_path), require_secrets=require_secrets)
        key = env.name.lower()
        if key in seen:
            raise ValueError(f"Duplicate environment name: {env.name}")
        seen.add(key)
        result.append(env)
    return result


def get_environment_by_name(
    environments: list[D365Environment],
    name: str,
) -> D365Environment:
    needle = name.strip().lower()
    for env in environments:
        if env.name.lower() == needle:
            return env
    available = ", ".join(e.name for e in environments)
    raise ValueError(f"Unknown environment {name!r}. Available: {available}")
