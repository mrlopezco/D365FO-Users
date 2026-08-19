"""D365 F&O OData client (Entra client credentials, GET/POST)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def normalize_environment_url(url: str) -> str:
    return url.strip().rstrip("/")


def acquire_fo_access_token(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    environment_url: str,
    timeout_sec: float = 30.0,
) -> str:
    """Client credentials token; scope is {environment_url}/.default."""
    scope = f"{normalize_environment_url(environment_url)}/.default"
    token_url = (
        f"https://login.microsoftonline.com/{tenant_id.strip()}/oauth2/v2.0/token"
    )
    body = urllib.parse.urlencode(
        {
            "client_id": client_id.strip(),
            "client_secret": client_secret,
            "scope": scope,
            "grant_type": "client_credentials",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        token_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Entra token request failed (HTTP {e.code}): {detail}") from e

    data = json.loads(raw)
    token = data.get("access_token")
    if not token:
        desc = data.get("error_description") or data.get("error") or raw
        raise RuntimeError(f"Entra did not return an access token: {desc}")
    return str(token)


def _collect_error_messages(node: Any, out: list[str]) -> None:
    """Walk F&O OData error.innererror / internalexception chains."""
    if not isinstance(node, dict):
        return
    for key in ("message", "Message"):
        raw = node.get(key)
        if isinstance(raw, str):
            text = raw.strip()
            if text and text not in out:
                out.append(text)
    for nested_key in ("innererror", "internalexception", "InternalException"):
        nested = node.get(nested_key)
        if nested is not None:
            _collect_error_messages(nested, out)


def extract_fo_error_message(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if not isinstance(err, dict):
        return None
    messages: list[str] = []
    _collect_error_messages(err, messages)
    if not messages:
        return None
    if len(messages) == 1:
        return messages[0]
    # Outermost is often generic; prefer the deepest distinct message for one-liners
    return f"{messages[0]} → {messages[-1]}"


def format_fo_error_response(
    status: int,
    body: Any,
    *,
    verbose: bool = False,
) -> str:
    """Human-readable error text for CLI output."""
    lines: list[str] = [f"HTTP {status}"]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            messages: list[str] = []
            _collect_error_messages(err, messages)
            if len(messages) > 1:
                lines.append("Error chain:")
                for i, msg in enumerate(messages, start=1):
                    lines.append(f"  {i}. {msg}")
            elif messages:
                lines.append(messages[0])
            code = err.get("code")
            if code:
                lines.append(f"Code: {code}")
        elif body.get("error"):
            lines.append(str(body["error"]))
    summary = extract_fo_error_message(body)
    if summary and summary not in lines[-1] if lines else True:
        if len(lines) == 1 or lines[0].startswith("HTTP"):
            if len(lines) == 1:
                lines.append(summary)
    if verbose and body is not None:
        lines.append("Full response body:")
        lines.append(json.dumps(body, indent=2, default=str))
    elif not verbose and isinstance(body, dict):
        lines.append(
            "Re-run with --verbose to print the full OData JSON error body."
        )
    return "\n".join(lines)


def _odata_headers(
    access_token: str,
    *,
    company: str | None = None,
    content_type: str | None = None,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
    }
    if company:
        headers["Company"] = company.strip().upper()
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def odata_request_json(
    url: str,
    *,
    access_token: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    company: str | None = None,
    timeout_sec: float = 60.0,
) -> tuple[int, Any]:
    data: bytes | None = None
    content_type: str | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        content_type = "application/json"

    headers = _odata_headers(
        access_token, company=company, content_type=content_type
    )
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return status, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return status, {"error": {"message": raw or f"HTTP {status}"}}

    if not raw:
        return status, None
    return status, json.loads(raw)


def odata_get_json(
    url: str,
    *,
    access_token: str,
    company: str | None = None,
    timeout_sec: float = 60.0,
) -> tuple[int, Any]:
    return odata_request_json(
        url,
        access_token=access_token,
        method="GET",
        company=company,
        timeout_sec=timeout_sec,
    )


def odata_post_json(
    url: str,
    *,
    access_token: str,
    body: dict[str, Any],
    company: str | None = None,
    timeout_sec: float = 120.0,
) -> tuple[int, Any]:
    return odata_request_json(
        url,
        access_token=access_token,
        method="POST",
        body=body,
        company=company,
        timeout_sec=timeout_sec,
    )


def test_connection(
    environment_url: str,
    access_token: str,
) -> None:
    """Verify token and GET /data. Raises RuntimeError on auth or server errors."""
    base = normalize_environment_url(environment_url)
    status, body = odata_get_json(f"{base}/data", access_token=access_token)
    if status in (401, 403):
        raise RuntimeError(
            "Token OK but F&O rejected the call. Check Entra applications mapping "
            "and security roles on the mapped user."
        )
    if status >= 400 and status != 404:
        msg = extract_fo_error_message(body) or f"F&O /data returned HTTP {status}"
        raise RuntimeError(msg)


def entity_collection_url(environment_url: str, entity_name: str) -> str:
    base = normalize_environment_url(environment_url)
    return f"{base}/data/{urllib.parse.quote(entity_name.strip(), safe='')}"


def odata_escape_string_literal(value: str) -> str:
    return value.replace("'", "''")


def fetch_entity_rows(
    environment_url: str,
    entity_name: str,
    *,
    access_token: str,
    odata_filter: str | None = None,
    top: int | None = 1,
    company: str | None = None,
    cross_company: bool = False,
) -> list[dict[str, Any]]:
    base = normalize_environment_url(environment_url)
    entity_path = urllib.parse.quote(entity_name.strip(), safe="")
    params: list[tuple[str, str]] = []
    if odata_filter:
        params.append(("$filter", odata_filter))
    if top is not None:
        params.append(("$top", str(top)))
    if cross_company:
        params.append(("cross-company", "true"))
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{base}/data/{entity_path}{query}"
    status, body = odata_get_json(url, access_token=access_token, company=company)
    if status < 200 or status >= 300:
        msg = extract_fo_error_message(body) or f"HTTP {status}"
        raise RuntimeError(msg)
    if not isinstance(body, dict):
        return []
    value = body.get("value")
    if not isinstance(value, list):
        return []
    return [r for r in value if isinstance(r, dict)]


def _normalize_property_key(name: str) -> str:
    return re.sub(r"[_\s]", "", name).casefold()


def extract_party_number_from_row(row: dict[str, Any]) -> str | None:
    for key, val in row.items():
        if key.startswith("@"):
            continue
        if _normalize_property_key(key) == _normalize_property_key("PartyNumber"):
            if val is not None and str(val).strip():
                return str(val).strip()
    return None
