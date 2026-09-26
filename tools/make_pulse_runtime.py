#!/usr/bin/env python3
"""Build a trimmed Pulse runtime for CLOVER from a full Pulse install.

    python3 tools/make_pulse_runtime.py /path/to/pulse_engine  [dest=pulse_runtime]

Copies only what the engine needs to load a patient and run the ventilator model:
shared libraries, the Python bindings (cdm + engine), patient states, substances,
patients, environments, configs. Leaves out the 12 GB verification set, the test and
study drivers, Java, protoc and the plotting-only Python subpackages. Writes
MANIFEST.json (file list, sizes, sha256) and copies Pulse's license/notices.
"""
import hashlib
import json
import os
import shutil
import sys

BIN_FILES = ["libPulseC.so", "PulseConfiguration.json", "Serialization.json"]
BIN_GLOBS = ["PyPulse.cpython-*.so", "PyPulse*.pyd", "PulseC.dll", "libPulseC.dylib"]
BIN_DIRS = ["states", "substances", "patients", "environments", "config", "resource", "nutrition", "ecg"]
PY_KEEP = ["__init__.py", "cdm", "engine"]
SKIP_PY_DIRS = {"__pycache__"}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_tree(src, dst, files):
    for root, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_PY_DIRS]
        rel = os.path.relpath(root, src)
        for n in names:
            if n.endswith(".pyc"):
                continue
            s = os.path.join(root, n)
            d = os.path.join(dst, rel, n) if rel != "." else os.path.join(dst, n)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            files.append(d)


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    src = os.path.abspath(sys.argv[1])
    dst = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else "pulse_runtime")
    sbin, spy = os.path.join(src, "bin"), os.path.join(src, "python", "pulse")
    if not os.path.isdir(sbin) or not os.path.isdir(spy):
        sys.exit(f"{src} does not look like a Pulse install (need bin/ and python/pulse/)")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    files = []
    dbin = os.path.join(dst, "bin"); os.makedirs(dbin)
    import glob
    for n in BIN_FILES:
        p = os.path.join(sbin, n)
        if os.path.exists(p):
            shutil.copy2(p, dbin); files.append(os.path.join(dbin, n))
    for g in BIN_GLOBS:
        for p in glob.glob(os.path.join(sbin, g)):
            shutil.copy2(p, dbin); files.append(os.path.join(dbin, os.path.basename(p)))
    for d in BIN_DIRS:
        p = os.path.join(sbin, d)
        if os.path.isdir(p):
            copy_tree(p, os.path.join(dbin, d), files)
    dpy = os.path.join(dst, "python", "pulse"); os.makedirs(dpy)
    for k in PY_KEEP:
        p = os.path.join(spy, k)
        if os.path.isfile(p):
            shutil.copy2(p, dpy); files.append(os.path.join(dpy, k))
        elif os.path.isdir(p):
            copy_tree(p, os.path.join(dpy, k), files)
    for lic in ("LICENSE", "LICENSE.txt", "NOTICE", "NOTICE.txt", "README.md"):
        for base in (src, os.path.join(src, "bin"), os.path.join(src, "python")):
            p = os.path.join(base, lic)
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(dst, lic)); files.append(os.path.join(dst, lic)); break
    readme = os.path.join(dst, "RUNTIME_README.txt")
    with open(readme, "w") as fh:
        fh.write("CLOVER trimmed Pulse runtime\n"
                 f"Built from: {src}\n"
                 "Contents: Pulse Physiology Engine shared libraries, Python bindings (pulse.cdm, pulse.engine),\n"
                 "patient states, substances, patients, environments and configuration files.\n"
                 "Pulse is (c) Kitware, Inc. and licensed under the Apache License 2.0 (https://pulse.kitware.com).\n"
                 "Redistribute this folder with that license and Pulse's NOTICE file; nothing here is CLOVER code.\n"
                 "Verify integrity against MANIFEST.json (sha256 per file).\n")
    files.append(readme)
    manifest = {"source": src, "files": []}
    total = 0
    for f in sorted(files):
        sz = os.path.getsize(f); total += sz
        manifest["files"].append({"path": os.path.relpath(f, dst), "size": sz, "sha256": sha256(f)})
    manifest["total_bytes"] = total
    with open(os.path.join(dst, "MANIFEST.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    print(f"pulse_runtime written to {dst}: {len(files)} files, {total/1e6:.0f} MB (source {src})")


if __name__ == "__main__":
    main()
