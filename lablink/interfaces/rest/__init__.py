"""REST driver package."""

from lablink.interfaces.rest.config import RestDriverConfig
from lablink.interfaces.rest.driver import RestDriver

__all__ = ["RestDriver", "RestDriverConfig"]
