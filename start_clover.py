#!/usr/bin/env python3
"""Start CLOVER for a Pulse demo. Double-click this file, or run:  python3 start_clover.py

It finds the Python that has the Pulse bindings (the sibling vent_optimizer project's
.venv), re-launches itself there if needed, starts the bridge, and opens the control
page in your browser:

    http://localhost:8765/         pick a patient, start the session, inject scenarios
    http://localhost:8765/clover   the CLOVER medic display (also opened from the control page)

Close this window (or press Ctrl+C) to stop everything.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "bridge")
VENT_OPT = os.environ.get("CLOVER_VENT_OPTIMIZER") or os.path.normpath(os.path.join(HERE, "..", "vent_optimizer"))


def venv_python() -> str | None:
    for cand in (os.path.join(VENT_OPT, ".venv", "bin", "python"),
                 os.path.join(VENT_OPT, ".venv", "Scripts", "python.exe")):
        if os.path.exists(cand):
            return os.path.realpath(cand)   # avoids the venv's "unexpected sys.exec_prefix" warning
    return None


def pulse_available_here() -> bool:
    try:
        sys.path.insert(0, VENT_OPT)
        from common import pulse_env  # type: ignore
        pulse_env.bootstrap(chdir=False)
        import pulse.engine.PulseEngine  # type: ignore  # noqa: F401
        import websockets  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def main() -> None:
    if not pulse_available_here():
        py = venv_python()
        in_venv = os.path.normpath(sys.prefix) == os.path.normpath(os.path.join(VENT_OPT, ".venv"))
        if py and not in_venv and not os.environ.get("CLOVER_REEXEC"):
            print(f"Re-launching under {py} (it has the Pulse bindings)…")
            os.environ["CLOVER_REEXEC"] = "1"
            os.execv(py, [py, os.path.abspath(__file__)] + sys.argv[1:])
        print("Could not find the Pulse Physiology Engine.")
        print(f"Expected the vent_optimizer project with its .venv at: {VENT_OPT}")
        print("Set CLOVER_VENT_OPTIMIZER to its location, or install requirements into this Python.")
        input("Press Enter to close…")
        sys.exit(1)
    sys.path.insert(0, BRIDGE)
    from clover_bridge.server import main as bridge_main
    args = ["--http-port", "8765", "--ws-port", "8766", "--open", "--vent-optimizer", VENT_OPT] + sys.argv[1:]
    print("CLOVER bridge starting. Control page: http://localhost:8765/   CLOVER display: http://localhost:8765/clover")
    print("Close this window or press Ctrl+C to stop.")
    try:
        bridge_main(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
