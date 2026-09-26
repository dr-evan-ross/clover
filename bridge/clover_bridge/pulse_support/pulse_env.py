"""Find a Pulse runtime and make it importable.

Search order:
  1. CLOVER_PULSE_RUNTIME            (a trimmed runtime made by tools/make_pulse_runtime.py)
  2. <repo>/pulse_runtime            (same, in the default location)
  3. CLOVER_VENT_OPTIMIZER/pulse_engine, or ../vent_optimizer/pulse_engine (a full install)

Pulse loads its resource files relative to the current working directory, so
``bootstrap(chdir=True)`` changes into the runtime's bin directory.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))


def candidates() -> list[str]:
    out = []
    env = os.environ.get("CLOVER_PULSE_RUNTIME")
    if env:
        out.append(env)
    out.append(os.path.join(REPO, "pulse_runtime"))
    vo = os.environ.get("CLOVER_VENT_OPTIMIZER") or os.path.normpath(os.path.join(REPO, "..", "vent_optimizer"))
    out.append(os.path.join(vo, "pulse_engine"))
    return out


def find_runtime() -> str | None:
    for c in candidates():
        if os.path.isdir(os.path.join(c, "bin")) and os.path.isdir(os.path.join(c, "python", "pulse")):
            return os.path.abspath(c)
    return None


def bootstrap(chdir: bool = True) -> str:
    """Put the runtime's Python bindings and bin on sys.path; optionally chdir into bin.
    Returns the runtime root. Raises RuntimeError if none is found."""
    root = find_runtime()
    if root is None:
        raise RuntimeError("No Pulse runtime found. Set CLOVER_PULSE_RUNTIME, place a pulse_runtime/ folder "
                           "in the CLOVER repo (see tools/make_pulse_runtime.py), or keep vent_optimizer/pulse_engine next to the repo.")
    py, bn = os.path.join(root, "python"), os.path.join(root, "bin")
    for p in (py, bn):
        if p not in sys.path:
            sys.path.insert(0, p)
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(bn)
        except OSError:
            pass
    if chdir:
        os.chdir(bn)
    return root
