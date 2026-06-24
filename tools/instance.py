#!/usr/bin/env python3
"""Instance-directory resolution for the ExoCortex engine.

ExoCortex separates the *engine* (this installable package — the code under
``tools/``, the scaffolding, the docs; contains no personal data) from an
*instance* (a user's data dir: ``journal/``, ``raw/``, ``wiki/``,
``domains/``, and their contracts; created by ``exocortex init``). The engine
reads and writes an instance; the instance is the user's own (private) thing.

Because a pip-installed engine can live in ``site-packages`` while the
instance lives anywhere on disk, workers must not assume they sit *inside* the
instance. This module is the single place that answers "which instance dir am
I operating on?".

Resolution order (first match wins):

1. An explicit path (a ``--root`` / function argument).
2. The ``$EXOCORTEX_HOME`` environment variable.
3. The nearest ExoCortex instance root at or above the current working
   directory (walked upward looking for the instance markers).
4. A sensible default: the repo root two levels above this file
   (``parents[1]`` of ``tools/instance.py``), which is correct for an
   in-tree / editable install where the engine and instance coincide.

Every worker should default its ``--root`` to ``None`` and pass the parsed
value through :func:`resolve_instance_root`, so all four layers apply
uniformly.
"""

from __future__ import annotations

import os
from pathlib import Path

# Markers that identify a directory as an ExoCortex *instance* root. These are
# the durable structural pieces an instance always has once initialised. We
# require a subset to be present (not all) so a freshly-initialised instance is
# recognised even before every optional area exists.
INSTANCE_MARKERS = ("system", "journal", "wiki")

# The in-tree default: parents[0] == tools/, parents[1] == repo root.
ENGINE_ROOT = Path(__file__).resolve().parents[1]


def _looks_like_instance(path: Path) -> bool:
    """True if ``path`` has enough instance markers to be a real instance.

    Requires at least two of the markers so an unrelated directory that merely
    happens to contain a ``system/`` folder is not mistaken for an instance.
    """
    present = sum(1 for marker in INSTANCE_MARKERS if (path / marker).exists())
    return present >= 2


def find_instance_root_upward(start: Path) -> Path | None:
    """Walk from ``start`` upward, returning the first directory that looks
    like an ExoCortex instance, or ``None`` if none is found."""
    start = start.resolve()
    for candidate in (start, *start.parents):
        if _looks_like_instance(candidate):
            return candidate
    return None


def resolve_instance_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the active instance directory.

    See the module docstring for the full precedence. Always returns an
    absolute, resolved :class:`Path`; never raises for a missing instance
    (callers that need the instance to exist should check afterwards).
    """
    # 1. Explicit argument (e.g. --root).
    if explicit:
        return Path(explicit).expanduser().resolve()

    # 2. $EXOCORTEX_HOME.
    env_home = os.environ.get("EXOCORTEX_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()

    # 3. Nearest instance root at or above the current working directory.
    found = find_instance_root_upward(Path.cwd())
    if found is not None:
        return found

    # 4. In-tree default (engine and instance coincide).
    return ENGINE_ROOT


def default_root_arg() -> None:
    """Default value for a worker's ``--root`` argument.

    Workers should use ``default=None`` and resolve in ``main`` so that the
    full precedence chain (including ``$EXOCORTEX_HOME`` and the cwd walk)
    applies even when ``--root`` is omitted. This helper exists to document
    that intent at the call site.
    """
    return None
