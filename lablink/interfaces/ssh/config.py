"""SSH driver config.

SshDriverConfig inherits AuthConfig because SSH always needs credentials
(at minimum a username; typically a key or password).
"""

from dataclasses import dataclass

from lablink.base import AuthConfig, DriverConfig


@dataclass(kw_only=True)
class SshDriverConfig(DriverConfig, AuthConfig):
    """Config for an SSH target (server, embedded Linux, network device, etc.).

    host and username default to empty for dataclass construction safety;
    connect() surfaces clear errors when either is absent.
    """

    host: str = ""
    port: int = 22
    username: str = ""
