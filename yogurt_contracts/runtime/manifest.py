from typing import Tuple

from ..foundation.model import FrozenContract


class RuntimeManifest(FrozenContract):
    startup_dependencies: Tuple[str, ...] = ()
    functional_dependencies: Tuple[str, ...] = ()
    optional_bindings: Tuple[str, ...] = ()
