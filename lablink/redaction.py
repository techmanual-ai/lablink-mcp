"""Credential redaction for free-form event-log fields.

LabLink follows "credentials by reference only" (docs/ARCHITECTURE.md §2):
config files name an environment variable, never the secret itself. But a tool
*argument* can still carry a secret the agent inlined by mistake — most
dangerously an ``ssh_exec`` command like ``echo $PASS | sudo -S ...``. Free-form
fields such as ``command`` (SSH) and ``path`` (REST) are written verbatim to the
JSONL event log, which is a durable artifact outside the agent's control.

This module is the shared scrubber. ``secret_values(config)`` resolves the
secrets a session *could* expose from its ``AuthConfig`` env-var fields;
``redact(text, secrets)`` replaces any occurrence with ``***``. The scrubbing is
applied at the logging boundary — ``event_logger.log_event`` calls ``redact`` on
every free-form field when a driver passes ``secrets=`` — so a driver cannot
leak a known credential by forgetting to wrap an individual field.
``contains_secret`` is the cheap detector drivers use to decide whether to warn
the agent that it inlined a credential.

Limitations (both are by design, documented for honesty, not silently relied
upon):

- **Known secrets only.** This catches values held in the env vars the config
  references. A secret the agent invents inline from some other source is not in
  that set and passes through. The agent-facing docstring rules and the
  ``security_warning`` the SSH tools attach are the front-line defense;
  redaction is the safety net.
- **Literal-value matching.** A secret is matched as the raw ``os.environ``
  value. A *transformed* copy — percent-encoded in a URL query, base64 in a
  Basic-auth header, shell-quoted — will not match. Logged fields are matched
  as-is, so encode-then-log paths can still leak the encoded form.

Very short secrets (below ``_MIN_SECRET_LEN``) are excluded from the secret set:
blanket substring replacement of a 1–3 character value corrupts unrelated log
text far more than it protects, and such low-entropy values are not real
machine credentials.
"""

import os
from typing import Any

# AuthConfig fields that name an environment variable holding a secret value.
# (auth_ssh_key_path is a path, not a secret, and is intentionally excluded.)
_SECRET_ENV_FIELDS = (
    "auth_token_env",
    "auth_username_env",
    "auth_password_env",
    "auth_ssh_passphrase_env",
)

# Secrets shorter than this are not redacted: substring replacement of a very
# short value mangles incidental matches across the log, destroying the forensic
# value redaction exists to protect. Tune here if a deployment uses (and refuses
# to rotate) short credentials.
_MIN_SECRET_LEN = 6

_PLACEHOLDER = "***"


def secret_values(config: Any) -> set[str]:
    """Return the set of secret strings a session for ``config`` could expose.

    For each ``auth_*_env`` field present on the config, resolve the named
    environment variable and collect its value. ``os.environ`` is read live,
    consistent with how the drivers resolve auth at connect time. Values shorter
    than ``_MIN_SECRET_LEN`` are excluded (see module docstring).

    Args:
        config: A driver config. Configs without ``AuthConfig`` fields (VISA,
            serial, python_shell) simply yield no secrets.

    Returns:
        A set of secret values. Empty when the config has no auth fields, the
        referenced variables are unset/blank, or every value is too short to
        redact safely.
    """
    secrets: set[str] = set()
    for field_name in _SECRET_ENV_FIELDS:
        env_var = getattr(config, field_name, None)
        if not env_var or not isinstance(env_var, str):
            continue
        value = os.environ.get(env_var)
        if value and len(value) >= _MIN_SECRET_LEN:
            secrets.add(value)
    return secrets


def contains_secret(text: Any, secrets: set[str]) -> bool:
    """Return True iff any secret appears verbatim in ``text``.

    The cheap detector for deciding whether to attach a ``security_warning`` to
    the agent-facing result. Does not allocate a scrubbed copy.

    Args:
        text: The string to inspect. Non-string input yields False.
        secrets: Secret values to look for, typically from ``secret_values``.
    """
    if not isinstance(text, str) or not text or not secrets:
        return False
    return any(secret and secret in text for secret in secrets)


def redact(text: Any, secrets: set[str]) -> tuple[Any, bool]:
    """Replace every occurrence of any secret in ``text`` with ``***``.

    Args:
        text: The string to scrub. Non-string input (e.g. None) is returned
            unchanged with ``found=False``.
        secrets: Secret values to remove, typically from ``secret_values``.

    Returns:
        ``(scrubbed_text, found)`` where ``found`` is True iff at least one
        secret was present. Replacement is longest-secret-first so that a secret
        which is a substring of another is handled without leaving fragments.
    """
    if not isinstance(text, str) or not text or not secrets:
        return text, False

    found = False
    scrubbed = text
    for secret in sorted(secrets, key=len, reverse=True):
        if secret and secret in scrubbed:
            scrubbed = scrubbed.replace(secret, _PLACEHOLDER)
            found = True
    return scrubbed, found
