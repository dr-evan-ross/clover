#!/usr/bin/env python3
"""Start CLOVER for a Pulse demo. Double-click "CLOVER Demo.desktop", start_clover.sh, or run:
    python3 start_clover.py

It needs a Pulse runtime (pulse_runtime/ in this folder, or vent_optimizer/pulse_engine
next to it) and a Python with Pulse's dependencies. On first run it creates its own
virtual environment (.venv) and installs requirements-pulse.txt, so nothing has to be
set up by hand. Then it starts the bridge and opens the control page:

    http://localhost:8765/         pick a patient, start the session, inject scenarios
    http://localhost:8765/clover   the CLOVER medic display

Close this window (or press Ctrl+C) to stop everything.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "bridge")
VENV = os.path.join(HERE, ".venv")
REQS = os.path.join(HERE, "requirements-pulse.txt")
VENT_OPT = os.environ.get("CLOVER_VENT_OPTIMIZER") or os.path.normpath(os.path.join(HERE, "..", "vent_optimizer"))


def venv_python(venv: str) -> str | None:
    for cand in (os.path.join(venv, "bin", "python"), os.path.join(venv, "Scripts", "python.exe")):
        if os.path.exists(cand):
            return os.path.abspath(cand)   # keep the venv path itself; resolving the symlink leaves the venv
    return None


def runtime_present() -> str | None:
    sys.path.insert(0, BRIDGE)
    from clover_bridge.pulse_support import pulse_env
    return pulse_env.find_runtime()


def deps_present() -> bool:
    try:
        from clover_bridge.pulse_support import pulse_env
        pulse_env.bootstrap(chdir=False)
        import pulse.engine.PulseEngine  # type: ignore  # noqa: F401
        import websockets  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def in_venv(venv: str) -> bool:
    return os.path.normpath(sys.prefix) == os.path.normpath(venv)


def ensure_own_venv() -> str:
    """Create .venv with requirements-pulse.txt if missing; return its python."""
    py = venv_python(VENV)
    if py is None:
        print(f"First run: creating {VENV} and installing Pulse dependencies (one-time, needs internet)…")
        subprocess.check_call([sys.executable, "-m", "venv", VENV])
        py = venv_python(VENV)
        subprocess.check_call([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
        subprocess.check_call([py, "-m", "pip", "install", "--quiet", "-r", REQS])
    return py


def main() -> None:
    rt = runtime_present()
    if rt is None:
        print("No Pulse runtime found.")
        print(f"  Put a pulse_runtime/ folder in {HERE} (build one with tools/make_pulse_runtime.py),")
        print(f"  or keep the vent_optimizer project at {VENT_OPT}, or set CLOVER_PULSE_RUNTIME.")
        input("Press Enter to close…"); sys.exit(1)
    if not deps_present() and not os.environ.get("CLOVER_REEXEC"):
        # prefer our own venv; fall back to vent_optimizer's if it exists
        py = None
        try:
            py = ensure_own_venv()
        except Exception as e:
            print(f"Could not set up .venv ({e}); trying vent_optimizer's venv…")
            py = venv_python(os.path.join(VENT_OPT, ".venv"))
        if py and not in_venv(os.path.dirname(os.path.dirname(py))):
            os.environ["CLOVER_REEXEC"] = "1"
            print(f"Re-launching under {py}…")
            os.execv(py, [py, os.path.abspath(__file__)] + sys.argv[1:])
    if not deps_present():
        print("Pulse dependencies are missing in this Python. Install with:  pip install -r requirements-pulse.txt")
        input("Press Enter to close…"); sys.exit(1)
    from clover_bridge.server import main as bridge_main
    args = ["--http-port", "8765", "--ws-port", "8766", "--open"] + sys.argv[1:]
    print(f"CLOVER bridge starting with Pulse runtime {rt}")
    print("Control page: http://localhost:8765/   CLOVER display: http://localhost:8765/clover")
    print("Close this window or press Ctrl+C to stop.")
    try:
        bridge_main(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
