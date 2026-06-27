import time
from .mini import run_remote
from .preconditions import is_aqua

READY_MARKERS = ["47990", "Configuration", "Async", "Service", "Started"]
CAPTURE_FAIL_MARKERS = ["SCShareableContent failed", "No screen capture",
                        "Screen Recording", "failed to create SCStream",
                        "getShareableContent"]


def log_ready(log_text: str) -> bool:
    return any(m in log_text for m in READY_MARKERS)


def log_capture_failed(log_text: str) -> bool:
    return any(m in log_text for m in CAPTURE_FAIL_MARKERS)


def _asuser(uid: int, user: str, cmd: str) -> str:
    """Run cmd as <user> inside the console Aqua session.

    `launchctl asuser` requires root (passwordless sudo for /bin/launchctl) and
    runs the command AS ROOT; the inner `sudo -u <user>` drops to the console
    user (no password: root invoking sudo never prompts). Per-user TCC + config
    require Lumen to run as the user, not root.
    """
    return f"sudo -n launchctl asuser {uid} sudo -u {user} {cmd}"


def assert_aqua(ssh_host: str, brew_prefix: str, uid: int, user: str) -> None:
    out = run_remote(ssh_host, brew_prefix,
                     _asuser(uid, user, "launchctl managername")).stdout
    if not is_aqua(out):
        raise RuntimeError(f"refusing to run: managername={out.strip()!r}, need Aqua")


def launch(ssh_host: str, brew_prefix: str, uid: int, user: str, build_dir: str,
           conf_path: str, log_file: str) -> None:
    """Launch sunshine as <user> inside the Aqua session, kept awake by caffeinate."""
    assert_aqua(ssh_host, brew_prefix, uid, user)
    inner = (f"export SUNSHINE_ASSETS_DIR={build_dir}/assets; "
             f"nohup caffeinate -dimsu {build_dir}/sunshine {conf_path} "
             f">> {log_file} 2>&1 &")
    run_remote(ssh_host, brew_prefix, _asuser(uid, user, f"bash -lc {inner!r}"))


def wait_ready(ssh_host: str, brew_prefix: str, log_file: str, timeout: int = 90) -> None:
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


def teardown(ssh_host: str, brew_prefix: str, uid: int, user: str) -> None:
    run_remote(ssh_host, brew_prefix, _asuser(uid, user, "pkill -x sunshine"), check=False)
    run_remote(ssh_host, brew_prefix, _asuser(uid, user, "pkill -x caffeinate"), check=False)
