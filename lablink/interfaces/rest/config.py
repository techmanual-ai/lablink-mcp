"""REST driver config.

RestDriverConfig inherits AuthConfig because REST APIs commonly require auth
(bearer token, API key, or basic). auth_type defaults to "none" so unauthenticated
APIs work without any extra config fields.
"""

from dataclasses import dataclass

from lablink.base import AuthConfig, DriverConfig


@dataclass(kw_only=True)
class RestDriverConfig(DriverConfig, AuthConfig):
    """Config for a REST API target.

    base_url is the root of the API (e.g. "https://daq.local/api/v1").
    Tool calls append their path argument to this root. Trailing slashes
    on base_url and leading slashes on path are normalized at call time.

    Supported auth_type values:
        none        — no auth headers (default)
        bearer      — Authorization: Bearer <auth_token_env value>
        api_key     — X-API-Key: <auth_token_env value>
        basic       — HTTP Basic auth from auth_username_env / auth_password_env

    All credential values must be in environment variables — never in the config.
    """

    base_url: str = ""
    verify_ssl: bool = True
