# Harness Foundation (Plan 1: Build + Aqua Launch + Logging) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single command on the M5 Max dev box that builds the current Lumen repo on the M4 mini, launches it correctly inside the console Aqua session (gated so a broken/headless capture can never be silently measured), captures full verbose logs, and tears everything down cleanly.

**Architecture:** A small Python orchestrator (`harness/runner/`) drives the mini over SSH. Pure-logic helpers (command wrapping, output parsing, config/flag rendering) are unit-tested with pytest; infrastructure actions (rsync, brew, cmake, codesign, launchctl) are driven via subprocess and validated with explicit verification commands. Lumen is launched with `launchctl asuser <consoleUID>` so it joins the WindowServer/Aqua session that ScreenCaptureKit and CGVirtualDisplay require; every run is gated on `launchctl managername == Aqua`.

**Tech Stack:** Python 3 (stdlib + pytest), bash/ssh/rsync, Homebrew, CMake, `codesign`, `launchctl`, `caffeinate`/`pmset`. Targets macOS 26 / Apple Silicon.

**Source of truth:** `docs/superpowers/specs/2026-06-26-measurement-harness-design.md` (esp. §3 verified constraints). Reference build flags: `install.sh:127-159`.

**Conventions:**
- All paths absolute. Dev-box repo: `/Users/hazemeissa/Projects/lumen`. Mini deploy dir: `/Volumes/T7/lumen-harness/Lumen`. Mini build dir: `/Volumes/T7/lumen-harness/Lumen/build`. Console UID is resolved at runtime (currently 501).
- Every `ssh mac-mini` command that touches Homebrew/cmake must be wrapped by `remote_cmd()` (Task 2) so Homebrew is on PATH.
- A project venv lives at `harness/.venv` (Homebrew's Python 3.14 is externally-managed / PEP-668). Use it for ALL harness commands, e.g. `harness/.venv/bin/python -m pytest harness/tests -v` and `harness/.venv/bin/python -m harness.runner.loop`, run from the repo root. (Where steps below write `python3`, use the venv python.)

---

### Task 1: Scaffold the harness package

**Files:**
- Create: `harness/__init__.py` (empty)
- Create: `harness/runner/__init__.py` (empty)
- Create: `harness/tests/__init__.py` (empty)
- Create: `harness/config.toml`
- Create: `harness/.gitignore`
- Create: `harness/tests/test_smoke.py`

- [ ] **Step 1: Create the package files and config**

`harness/config.toml`:
```toml
[mini]
ssh_host = "mac-mini"
brew_prefix = "/opt/homebrew"
deploy_dir = "/Volumes/T7/lumen-harness/Lumen"
build_dir  = "/Volumes/T7/lumen-harness/Lumen/build"

[signing]
identity = "Lumen Dev"          # stable self-signed code-signing identity (Task 7)

[run]
config_dir = "/Users/hazemeissa/.config/sunshine"   # appdata on the mini (state/creds/log live here)
min_log_level = 0               # 0 = verbose (per-frame Sent Frame seq + debug latency loggers)
idle_seconds = 20               # how long to let Lumen run in this Plan-1 smoke loop
```

`harness/.gitignore`:
```
reports/
*.log
__pycache__/
```

`harness/tests/test_smoke.py`:
```python
def test_pytest_runs():
    assert True
```

- [ ] **Step 2: Verify pytest is wired up**

Run: `python3 -m pytest harness/tests -v`
Expected: `test_smoke.py::test_pytest_runs PASSED`. If pytest is missing: `python3 -m pip install --user pytest` then re-run.

- [ ] **Step 3: Commit**

```bash
git add harness/
git commit -m "chore(harness): scaffold harness package and config"
```

---

### Task 2: Remote command wrapper (Homebrew-aware SSH)

**Files:**
- Create: `harness/runner/mini.py`
- Create: `harness/tests/test_mini.py`

- [ ] **Step 1: Write the failing test**

`harness/tests/test_mini.py`:
```python
from harness.runner.mini import remote_cmd

def test_remote_cmd_loads_brew_env():
    out = remote_cmd("/opt/homebrew", "cmake --version")
    assert 'eval "$(/opt/homebrew/bin/brew shellenv)"' in out
    assert out.strip().endswith("cmake --version")

def test_remote_cmd_is_single_shell_string():
    out = remote_cmd("/opt/homebrew", "brew --prefix boost")
    assert "\n" not in out  # one-liner safe to pass to `ssh host '<cmd>'`
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest harness/tests/test_mini.py -v`
Expected: FAIL with `ModuleNotFoundError`/`ImportError: cannot import name 'remote_cmd'`.

- [ ] **Step 3: Implement `mini.py`**

`harness/runner/mini.py`:
```python
import subprocess

def remote_cmd(brew_prefix: str, cmd: str) -> str:
    """Wrap a command so Homebrew is on PATH in a non-interactive SSH shell."""
    return f'eval "$({brew_prefix}/bin/brew shellenv)"; {cmd}'

def run_remote(ssh_host: str, brew_prefix: str, cmd: str, check: bool = True,
               timeout: int = 1800) -> subprocess.CompletedProcess:
    wrapped = remote_cmd(brew_prefix, cmd)
    return subprocess.run(["ssh", ssh_host, wrapped], capture_output=True,
                          text=True, check=check, timeout=timeout)

def run_remote_stream(ssh_host: str, brew_prefix: str, cmd: str):
    """Run a remote command, streaming combined output to this process's stdout."""
    wrapped = remote_cmd(brew_prefix, cmd)
    return subprocess.Popen(["ssh", ssh_host, wrapped],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest harness/tests/test_mini.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Integration check (live)**

Run: `python3 -c "from harness.runner.mini import run_remote; print(run_remote('mac-mini','/opt/homebrew','brew --prefix').stdout.strip())"`
Expected: prints `/opt/homebrew`.

- [ ] **Step 6: Commit**

```bash
git add harness/runner/mini.py harness/tests/test_mini.py
git commit -m "feat(harness): Homebrew-aware remote command wrapper"
```

---

### Task 3: Preconditions (console session, deps, disk)

**Files:**
- Create: `harness/runner/preconditions.py`
- Create: `harness/tests/test_preconditions.py`

- [ ] **Step 1: Write the failing test**

`harness/tests/test_preconditions.py`:
```python
from harness.runner.preconditions import parse_console_uid, is_aqua, missing_deps

def test_parse_console_uid():
    assert parse_console_uid("501\n") == 501

def test_is_aqua_true():
    assert is_aqua("Aqua\n") is True

def test_is_aqua_false_for_background():
    assert is_aqua("Background\n") is False

def test_missing_deps_flags_absent_packages():
    # `brew list --versions X` prints a line if present, nothing if absent
    listing = {"boost": "", "llvm": "", "cmake": "cmake 4.2.3"}
    assert set(missing_deps(listing)) == {"boost", "llvm"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest harness/tests/test_preconditions.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `preconditions.py`**

`harness/runner/preconditions.py`:
```python
from .mini import run_remote

REQUIRED_DEPS = ["cmake", "boost", "pkg-config", "openssl@3", "opus", "llvm",
                 "doxygen", "graphviz", "node", "icu4c@78", "miniupnpc"]

def parse_console_uid(stat_output: str) -> int:
    return int(stat_output.strip())

def is_aqua(managername_output: str) -> bool:
    return managername_output.strip() == "Aqua"

def missing_deps(listing: dict[str, str]) -> list[str]:
    """listing maps dep -> output of `brew list --versions dep` ('' if absent)."""
    return [d for d, v in listing.items() if not v.strip()]

def console_uid(ssh_host: str, brew_prefix: str) -> int:
    return parse_console_uid(run_remote(ssh_host, brew_prefix, "stat -f%u /dev/console").stdout)

def console_user_present(ssh_host: str, brew_prefix: str) -> bool:
    out = run_remote(ssh_host, brew_prefix,
                     "scutil <<< 'show State:/Users/ConsoleUser' | awk '/Name :/{print $3}'").stdout
    return out.strip() not in ("", "loginwindow")

def aqua_session_ready(ssh_host: str, brew_prefix: str, uid: int) -> bool:
    # asuser needs root; passwordless sudo for /bin/launchctl via sudoers.d (one-time setup)
    out = run_remote(ssh_host, brew_prefix,
                     f"sudo -n launchctl asuser {uid} launchctl managername").stdout
    return is_aqua(out)

def check_deps(ssh_host: str, brew_prefix: str) -> list[str]:
    listing = {}
    for d in REQUIRED_DEPS:
        out = run_remote(ssh_host, brew_prefix, f"brew list --versions {d} | head -1",
                         check=False).stdout
        listing[d] = out
    return missing_deps(listing)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest harness/tests/test_preconditions.py -v`
Expected: all 4 PASS.

- [ ] **Step 5: Integration check (live)**

Run: `python3 -c "from harness.runner.preconditions import *; print('uid',console_uid('mac-mini','/opt/homebrew')); print('user',console_user_present('mac-mini','/opt/homebrew')); print('aqua',aqua_session_ready('mac-mini','/opt/homebrew',console_uid('mac-mini','/opt/homebrew'))); print('missing',check_deps('mac-mini','/opt/homebrew'))"`
Expected: `uid 501`, `user True`, `aqua True`, `missing ['boost', 'llvm']` (boost/llvm until Task 5).

- [ ] **Step 6: Commit**

```bash
git add harness/runner/preconditions.py harness/tests/test_preconditions.py
git commit -m "feat(harness): preconditions (console session, deps, disk)"
```

---

### Task 4: Deploy the working tree to the mini (rsync)

**Files:**
- Modify: `harness/runner/mini.py` (add `rsync_deploy`)
- Create: `harness/tests/test_deploy.py`

- [ ] **Step 1: Write the failing test**

`harness/tests/test_deploy.py`:
```python
from harness.runner.mini import rsync_excludes

def test_rsync_excludes_cover_heavy_and_local_dirs():
    ex = rsync_excludes()
    for d in [".git/", "build/", "harness/reports/", "third-party/*/build/"]:
        assert d in ex
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest harness/tests/test_deploy.py -v`
Expected: FAIL with `ImportError: cannot import name 'rsync_excludes'`.

- [ ] **Step 3: Implement in `mini.py`**

Append to `harness/runner/mini.py`:
```python
def rsync_excludes() -> list[str]:
    # harness/ is dev-box-only (incl. the venv with absolute symlinks); the mini
    # only needs the Lumen source to build.
    return [".git/", "build/", "harness/", "harness/reports/", "third-party/*/build/",
            "*.log", "__pycache__/", ".venv/", ".DS_Store"]

def rsync_deploy(local_dir: str, ssh_host: str, deploy_dir: str) -> subprocess.CompletedProcess:
    args = ["rsync", "-az", "--delete"]
    for ex in rsync_excludes():
        args += ["--exclude", ex]
    # ensure remote parent exists; trailing slash copies contents into deploy_dir
    subprocess.run(["ssh", ssh_host, f"mkdir -p {deploy_dir}"], check=True)
    args += [f"{local_dir.rstrip('/')}/", f"{ssh_host}:{deploy_dir}/"]
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=1800)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest harness/tests/test_deploy.py -v`
Expected: PASS.

- [ ] **Step 5: Integration check (live)**

Run: `python3 -c "from harness.runner.mini import rsync_deploy; rsync_deploy('/Users/hazemeissa/Projects/lumen','mac-mini','/Volumes/T7/lumen-harness/Lumen'); print('ok')"`
Then: `ssh mac-mini 'ls /Volumes/T7/lumen-harness/Lumen/CMakeLists.txt && test ! -e /Volumes/T7/lumen-harness/Lumen/.git && echo CLEAN'`
Expected: prints `ok`, the CMakeLists path, and `CLEAN` (no `.git` copied).

- [ ] **Step 6: Commit**

```bash
git add harness/runner/mini.py harness/tests/test_deploy.py
git commit -m "feat(harness): rsync deploy of working tree to the mini"
```

---

### Task 5: Install missing build dependencies (boost, llvm)

**Files:**
- Create: `harness/runner/deps.py`

- [ ] **Step 1: Implement `deps.py`**

`harness/runner/deps.py`:
```python
from .mini import run_remote
from .preconditions import check_deps

def ensure_deps(ssh_host: str, brew_prefix: str) -> list[str]:
    missing = check_deps(ssh_host, brew_prefix)
    if missing:
        run_remote(ssh_host, brew_prefix, "brew install " + " ".join(missing), timeout=3600)
    return check_deps(ssh_host, brew_prefix)  # should be [] after install
```

- [ ] **Step 2: Run it (live) — this performs the install**

Run: `python3 -c "from harness.runner.deps import ensure_deps; print('still missing:', ensure_deps('mac-mini','/opt/homebrew'))"`
Expected: `still missing: []` (installs boost + llvm; may take several minutes).

- [ ] **Step 3: Verify disk did not fill**

Run: `ssh mac-mini 'df -h /System/Volumes/Data | tail -1'`
Expected: still has free space (>5Gi). If critically low, free space before proceeding.

- [ ] **Step 4: Commit**

```bash
git add harness/runner/deps.py
git commit -m "feat(harness): ensure build dependencies (boost, llvm)"
```

---

### Task 6: Build Lumen on the mini

**Files:**
- Create: `harness/runner/build.py`
- Create: `harness/tests/test_build.py`

- [ ] **Step 1: Write the failing test**

`harness/tests/test_build.py`:
```python
from harness.runner.build import build_cmake_flags

def test_cmake_flags_include_toolchain_fixes():
    flags = build_cmake_flags(sdk_path="/SDK", openssl_prefix="/ossl",
                              assets_dir="/Volumes/T7/lumen-harness/Lumen/build/assets")
    joined = " ".join(flags)
    assert "-DCMAKE_BUILD_TYPE=Release" in joined
    assert "-DOPENSSL_ROOT_DIR=/ossl" in joined
    assert "-nostdinc++" in joined and "/SDK/usr/include/c++/v1" in joined
    assert "-std=gnu++2b" in joined
    assert "-DCMAKE_OSX_SYSROOT=/SDK" in joined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest harness/tests/test_build.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `build.py`** (flags mirror `install.sh:138-151`)

`harness/runner/build.py`:
```python
from .mini import run_remote

def build_cmake_flags(sdk_path: str, openssl_prefix: str, assets_dir: str) -> list[str]:
    cxx = f"/usr/include/c++/v1"
    return [
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_WERROR=ON",
        f"-DOPENSSL_ROOT_DIR={openssl_prefix}",
        f"-DSUNSHINE_ASSETS_DIR={assets_dir}",
        "-DSUNSHINE_BUILD_HOMEBREW=ON",
        "-DSUNSHINE_ENABLE_TRAY=ON",
        "-DBOOST_USE_STATIC=OFF",
        f"-DCMAKE_OSX_SYSROOT={sdk_path}",
        f"-DCMAKE_CXX_FLAGS=-nostdinc++ -cxx-isystem {sdk_path}{cxx} -std=gnu++2b -I{openssl_prefix}/include",
        f"-DCMAKE_C_FLAGS=-I{openssl_prefix}/include",
    ]

def build(ssh_host: str, brew_prefix: str, deploy_dir: str, build_dir: str):
    sdk = run_remote(ssh_host, brew_prefix, "xcrun --show-sdk-path").stdout.strip()
    ossl = run_remote(ssh_host, brew_prefix, "brew --prefix openssl@3").stdout.strip()
    flags = build_cmake_flags(sdk, ossl, f"{build_dir}/assets")
    cfg = (f"mkdir -p {build_dir} && cd {build_dir} && "
           f"cmake {' '.join(repr(f) for f in flags)} {deploy_dir}")
    run_remote(ssh_host, brew_prefix, cfg, timeout=1200)
    make = f"cd {build_dir} && make sunshine vd_helper -j$(sysctl -n hw.ncpu)"
    run_remote(ssh_host, brew_prefix, make, timeout=3600)
    return f"{build_dir}/sunshine"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest harness/tests/test_build.py -v`
Expected: PASS.

- [ ] **Step 5: Integration check (live) — first real build**

Run: `python3 -c "from harness.runner.build import build; print(build('mac-mini','/opt/homebrew','/Volumes/T7/lumen-harness/Lumen','/Volumes/T7/lumen-harness/Lumen/build'))"`
Then: `ssh mac-mini 'ls -l /Volumes/T7/lumen-harness/Lumen/build/sunshine* /Volumes/T7/lumen-harness/Lumen/build/vd_helper'`
Expected: a `sunshine` binary and `vd_helper` exist.
**Known risk:** clang 21 / macOS 26.4 SDK is newer than the original Feb build. If the C++ flags need adjustment, debug here (use `superpowers:systematic-debugging`), update `build_cmake_flags`, re-run, and update this step + the spec.

- [ ] **Step 6: Commit**

```bash
git add harness/runner/build.py harness/tests/test_build.py
git commit -m "feat(harness): cmake/make build of sunshine + vd_helper on the mini"
```

---

### Task 7: Stable self-signed code-signing identity + signing

**Files:**
- Create: `harness/runner/sign.py`
- Create: `harness/scripts/create_signing_identity.sh`

- [ ] **Step 1: One-time identity creation script**

`harness/scripts/create_signing_identity.sh` (run ONCE on the mini; creates a code-signing identity named per `config.toml`):
```bash
#!/bin/bash
set -e
NAME="${1:-Lumen Dev}"
if security find-identity -v -p codesigning | grep -q "$NAME"; then
  echo "identity '$NAME' already exists"; exit 0
fi
TMP=$(mktemp -d)
cat > "$TMP/ext.cnf" <<EOF
[req]
distinguished_name=dn
x509_extensions=v3
prompt=no
[dn]
CN=$NAME
[v3]
basicConstraints=critical,CA:false
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
EOF
openssl req -x509 -newkey rsa:2048 -keyout "$TMP/key.pem" -out "$TMP/cert.pem" \
  -days 3650 -nodes -config "$TMP/ext.cnf"
openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
  -out "$TMP/id.p12" -passout pass:lumen -name "$NAME"
security import "$TMP/id.p12" -k ~/Library/Keychains/login.keychain-db \
  -P lumen -T /usr/bin/codesign
# allow codesign to use the key without prompting
security set-key-partition-list -S apple-tool:,apple: -s -k "" \
  ~/Library/Keychains/login.keychain-db >/dev/null 2>&1 || true
rm -rf "$TMP"
echo "created '$NAME'"
security find-identity -v -p codesigning | grep "$NAME"
```

- [ ] **Step 2: Create the identity on the mini (live, one-time)**

Run: `scp harness/scripts/create_signing_identity.sh mac-mini:/tmp/ && ssh mac-mini 'bash /tmp/create_signing_identity.sh "Lumen Dev"'`
Expected: prints `created 'Lumen Dev'` and a `find-identity` line containing `Lumen Dev`.
**Fallback if the scripted cert is not usable by codesign:** create it once via Keychain Access → Certificate Assistant → *Create a Certificate* (name `Lumen Dev`, Identity Type: Self Signed Root, Certificate Type: Code Signing) over Screen Sharing into the console session. Then continue.

- [ ] **Step 3: Implement `sign.py`**

`harness/runner/sign.py`:
```python
from .mini import run_remote

def sign_binaries(ssh_host: str, brew_prefix: str, build_dir: str, identity: str) -> None:
    for b in ("sunshine", "vd_helper"):
        run_remote(ssh_host, brew_prefix,
                   f'codesign --force --sign "{identity}" --timestamp=none {build_dir}/{b}')

def signed_identity(ssh_host: str, brew_prefix: str, build_dir: str) -> str:
    out = run_remote(ssh_host, brew_prefix,
                     f"codesign -dv {build_dir}/sunshine 2>&1 | grep -i 'Authority=' | head -1",
                     check=False).stdout
    return out.strip()
```

- [ ] **Step 4: Sign and verify (live)**

Run: `python3 -c "from harness.runner.sign import *; sign_binaries('mac-mini','/opt/homebrew','/Volumes/T7/lumen-harness/Lumen/build','Lumen Dev'); print(signed_identity('mac-mini','/opt/homebrew','/Volumes/T7/lumen-harness/Lumen/build'))"`
Expected: prints an `Authority=Lumen Dev` line (stable identity, not `Authority=(unavailable)` ad-hoc).

- [ ] **Step 5: Commit**

```bash
git add harness/runner/sign.py harness/scripts/create_signing_identity.sh
git commit -m "feat(harness): stable self-signed signing identity + binary signing"
```

---

### Task 8: Render the harness sunshine.conf

**Files:**
- Create: `harness/runner/config_render.py`
- Create: `harness/tests/test_config_render.py`

- [ ] **Step 1: Write the failing test**

`harness/tests/test_config_render.py`:
```python
from harness.runner.config_render import render_sunshine_conf

def test_render_sets_verbose_logging_and_virtual_display():
    text = render_sunshine_conf(min_log_level=0, log_file="/tmp/run/sunshine.log")
    assert "min_log_level = 0" in text
    assert "virtual_display = enabled" in text
    assert "audio_sink = system" in text
    assert "log_file = /tmp/run/sunshine.log" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest harness/tests/test_config_render.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `config_render.py`**

`harness/runner/config_render.py`:
```python
def render_sunshine_conf(min_log_level: int, log_file: str) -> str:
    return "\n".join([
        "# Lumen harness config (generated)",
        "audio_sink = system",
        "virtual_display = enabled",
        "upnp = disabled",                 # deterministic local runs; no NAT noise
        f"min_log_level = {min_log_level}",
        f"log_file = {log_file}",
        "",
    ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest harness/tests/test_config_render.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/runner/config_render.py harness/tests/test_config_render.py
git commit -m "feat(harness): render harness sunshine.conf (verbose logging)"
```

---

### Task 9: Launch into the Aqua session, gate, and detect readiness

**Files:**
- Create: `harness/runner/session.py`
- Create: `harness/tests/test_session.py`

- [ ] **Step 1: Write the failing test**

`harness/tests/test_session.py`:
```python
from harness.runner.session import log_ready, log_capture_failed

def test_log_ready_detects_listening():
    assert log_ready("...\nConfiguration UI available at https://...:47990\n") is True

def test_log_ready_false_when_not_yet():
    assert log_ready("Starting...\n") is False

def test_log_capture_failed_detects_sck_denial():
    assert log_capture_failed("error: SCShareableContent failed\n") is True

def test_log_capture_failed_false_on_clean():
    assert log_capture_failed("Capturing display 1\n") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest harness/tests/test_session.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `session.py`**

`harness/runner/session.py`:
```python
import time
from .mini import run_remote
from .preconditions import is_aqua

READY_MARKERS = ["47990", "Configuration", "Async encoder", "Service registered"]
CAPTURE_FAIL_MARKERS = ["SCShareableContent failed", "No screen capture",
                        "Screen Recording", "failed to create SCStream"]

def log_ready(log_text: str) -> bool:
    return any(m in log_text for m in READY_MARKERS)

def log_capture_failed(log_text: str) -> bool:
    return any(m in log_text for m in CAPTURE_FAIL_MARKERS)

def assert_aqua(ssh_host: str, brew_prefix: str, uid: int) -> None:
    out = run_remote(ssh_host, brew_prefix,
                     f"launchctl asuser {uid} launchctl managername").stdout
    if not is_aqua(out):
        raise RuntimeError(f"refusing to run: managername={out.strip()!r}, need Aqua")

def launch(ssh_host: str, brew_prefix: str, uid: int, build_dir: str,
           conf_path: str, log_file: str) -> None:
    """Launch sunshine inside the console Aqua session, kept awake by caffeinate."""
    assert_aqua(ssh_host, brew_prefix, uid)
    inner = (f"export SUNSHINE_ASSETS_DIR={build_dir}/assets; "
             f"caffeinate -dimsu {build_dir}/sunshine {conf_path} "
             f">> {log_file} 2>&1 &")
    run_remote(ssh_host, brew_prefix, f"launchctl asuser {uid} bash -lc {inner!r}")

def wait_ready(ssh_host: str, brew_prefix: str, log_file: str, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = run_remote(ssh_host, brew_prefix, f"cat {log_file} 2>/dev/null",
                          check=False).stdout
        if log_capture_failed(text):
            raise RuntimeError("capture failed (Screen Recording / SCK) — see log")
        if log_ready(text):
            return
        time.sleep(2)
    raise TimeoutError("sunshine did not reach ready state")

def teardown(ssh_host: str, brew_prefix: str, uid: int) -> None:
    run_remote(ssh_host, brew_prefix, f"launchctl asuser {uid} pkill -x sunshine",
               check=False)
    run_remote(ssh_host, brew_prefix, "pkill -x caffeinate", check=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest harness/tests/test_session.py -v`
Expected: all 4 PASS.
**Note:** `READY_MARKERS` / `CAPTURE_FAIL_MARKERS` are first guesses; during Task 11's live run, read the real log and adjust the marker strings to match Lumen's actual output, then re-run the tests.

- [ ] **Step 5: Commit**

```bash
git add harness/runner/session.py harness/tests/test_session.py
git commit -m "feat(harness): Aqua-gated launch, readiness + capture-failure detection, teardown"
```

---

### Task 10: Power/lock management (keep the session awake)

**Files:**
- Create: `harness/runner/power.py`

- [ ] **Step 1: Implement `power.py`**

`harness/runner/power.py`:
```python
from .mini import run_remote

def disable_sleep_lock(ssh_host: str, brew_prefix: str) -> None:
    run_remote(ssh_host, brew_prefix,
               "sudo pmset -a displaysleep 0 sleep 0 disablesleep 1", check=False)

def restore_sleep_lock(ssh_host: str, brew_prefix: str) -> None:
    run_remote(ssh_host, brew_prefix,
               "sudo pmset -a disablesleep 0 displaysleep 10 sleep 0", check=False)
```

- [ ] **Step 2: Verify (live)**

Run: `python3 -c "from harness.runner.power import *; disable_sleep_lock('mac-mini','/opt/homebrew')"` then `ssh mac-mini 'pmset -g | grep -E "disablesleep|displaysleep"'`
Expected: `disablesleep 1`, `displaysleep 0`.
**Note:** `sudo pmset` may require a one-time passwordless-sudo entry for `pmset` on the mini, or running it manually once. If it prompts, document the `sudoers` line and continue (non-blocking for Plan 1 since the console is already awake).

- [ ] **Step 3: Commit**

```bash
git add harness/runner/power.py
git commit -m "feat(harness): power/lock management for unattended capture"
```

---

### Task 11: Orchestrator — the one-command loop

**Files:**
- Create: `harness/runner/loop.py`
- Create: `harness/runner/runctx.py`

- [ ] **Step 1: Implement run context (paths + config load)**

`harness/runner/runctx.py`:
```python
import tomllib, time, os
from pathlib import Path

REPO = "/Users/hazemeissa/Projects/lumen"

def load_cfg():
    with open(f"{REPO}/harness/config.toml", "rb") as f:
        return tomllib.load(f)

def new_run_dir() -> Path:
    rid = time.strftime("%Y%m%d-%H%M%S")
    d = Path(REPO) / "harness" / "reports" / rid
    d.mkdir(parents=True, exist_ok=True)
    return d
```

- [ ] **Step 2: Implement `loop.py`**

`harness/runner/loop.py`:
```python
import sys, time, shlex
from .runctx import load_cfg, new_run_dir, REPO
from . import mini, preconditions as pre, deps, build as B, sign, config_render, session, power

def run():
    cfg = load_cfg()
    host = cfg["mini"]["ssh_host"]; bp = cfg["mini"]["brew_prefix"]
    deploy = cfg["mini"]["deploy_dir"]; bdir = cfg["mini"]["build_dir"]
    ident = cfg["signing"]["identity"]; cdir = cfg["run"]["config_dir"]
    mll = cfg["run"]["min_log_level"]; idle = cfg["run"]["idle_seconds"]
    rundir = new_run_dir()
    remote_log = f"{cdir}/harness-run.log"

    print(f"[1/8] preconditions"); uid = pre.console_uid(host, bp)
    assert pre.console_user_present(host, bp), "no console user logged in"
    assert pre.aqua_session_ready(host, bp, uid), "no Aqua session"
    print(f"[2/8] deploy"); mini.rsync_deploy(REPO, host, deploy)
    print(f"[3/8] deps"); assert deps.ensure_deps(host, bp) == [], "deps still missing"
    print(f"[4/8] build"); B.build(host, bp, deploy, bdir)
    print(f"[5/8] sign"); sign.sign_binaries(host, bp, bdir, ident)
    print(f"[6/8] config"); conf = config_render.render_sunshine_conf(mll, remote_log)
    mini.run_remote(host, bp, f"cat > {cdir}/harness.conf <<'EOF'\n{conf}\nEOF")
    mini.run_remote(host, bp, f": > {remote_log}")  # truncate
    try:
        power.disable_sleep_lock(host, bp)
        print(f"[7/8] launch + gate")
        session.launch(host, bp, uid, bdir, f"{cdir}/harness.conf", remote_log)
        session.wait_ready(host, bp, remote_log, timeout=90)
        print(f"      ready. idling {idle}s for log capture")
        time.sleep(idle)
    finally:
        print(f"[8/8] teardown")
        session.teardown(host, bp, uid)
        power.restore_sleep_lock(host, bp)
        local_log = rundir / "sunshine.log"
        out = mini.run_remote(host, bp, f"cat {remote_log}", check=False).stdout
        local_log.write_text(out)
        print(f"log saved: {local_log}  ({len(out.splitlines())} lines)")

if __name__ == "__main__":
    run()
```

- [ ] **Step 3: End-to-end run (live)**

Run: `python3 -m harness.runner.loop`
Expected: prints steps 1–8, `ready.`, and `log saved: …/sunshine.log (N lines)` with N>0. Open the saved log and confirm it contains the readiness marker and NO capture-failure marker, and ideally `Sent Frame seq [..]` lines once a client connects (clients arrive in Plan 3 — for Plan 1, success = Lumen launches in Aqua, reaches ready, and logs are captured).

- [ ] **Step 4: Adjust markers to reality**

Read the saved `sunshine.log`. Update `READY_MARKERS` / `CAPTURE_FAIL_MARKERS` in `session.py` to the actual strings Lumen prints; re-run `python3 -m pytest harness/tests/test_session.py -v` and the end-to-end run.

- [ ] **Step 5: Commit**

```bash
git add harness/runner/loop.py harness/runner/runctx.py
git commit -m "feat(harness): one-command build+launch+log-capture loop"
```

---

## Self-Review

**Spec coverage (§ of the design spec → task):**
- §5.2 build/deploy → Tasks 4,6 ✅
- §5.3 session launch + permissions + gate → Tasks 7,9,10 ✅
- §3.6 deps/disk → Tasks 3,5 ✅
- §3.1 Aqua gate + caffeinate + anti-frozen-capture → Task 9 (`assert_aqua`, `log_capture_failed`) ✅
- §3.2 stable signing → Task 7 ✅
- Full log capture → Task 11 ✅
- *Deferred to later plans (correctly out of scope for Plan 1):* per-frame host instrumentation + trace schema + reporter (§5.4/§5.7/§5.8 → Plan 2), client + workload + readback + dual-topology (§5.5/§5.6 → Plan 3), docs (§6 → Plan 4). Noted so the gap is intentional.

**Placeholder scan:** No TBD/TODO. Marker strings in Task 9 are explicitly first-guesses with a calibration step (Task 11 Step 4) — not silent placeholders.

**Type/name consistency:** `remote_cmd`/`run_remote` (mini.py) used everywhere; `console_uid`/`aqua_session_ready` (preconditions) used in loop.py; `build()` returns binary path; `sign_binaries(host,bp,build_dir,identity)` signature matches the loop call; `render_sunshine_conf(min_log_level, log_file)` matches; `launch(...)`/`wait_ready(...)`/`teardown(...)` signatures match loop.py. Consistent.

**Known execution risks (flagged in-task):** (a) cmake flags vs clang 21/macOS 26 SDK (Task 6 Step 5); (b) scripted signing identity usability (Task 7 Step 2 fallback); (c) passwordless `sudo pmset` (Task 10 Step 2); (d) readiness/failure marker strings (Task 11 Step 4).
