"""reqt has been renamed to reqstorm. This module re-exports reqstorm."""

import warnings

import reqstorm
from reqstorm import *  # noqa: F401,F403

__version__ = "2.0.1"
__all__ = [name for name in reqstorm.__all__ if name != "__version__"]

warnings.warn(
    "reqt has been renamed to reqstorm. Install reqstorm and use `import reqstorm`; "
    "the reqt package will not get further updates.",
    DeprecationWarning,
    stacklevel=2,
)
