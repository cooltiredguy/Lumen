import time
from .mini import run_remote
from .preconditions import is_aqua
from .launch_agent import PLIST_LABEL, agent_plist_path, render_plist

READY_MARKERS = ["Configuration UI available", "Starting main loop", "registered DNS service"]
CAPTURE_FAIL_MARKERS = ["SCShareableContent failed", "No screen capture",
                        "Screen Recording", "failed to create SCStream",
                        "declined TCC", "find working encoder"]


def log_ready(log_text: str) -> bool:
    return any(m in log_text for m in READY_MARKERS)


def log_capture_failed(log_text: str) -> bool:
    return any(m in log_text for m in CAPTURE_FAIL_MARKERS)


def _asuser(uid: int, user: str, cmd: str) -> str:
    """Run cmd as <user> inside the console Aqua session (asuser=root, drop to user)."""
    return f"sudo -n launchctl asuser {uid} sudo -u {user} {cmd}"


def assert_aqua(ssh_host: str, brew_prefix: str, uid: int, user: str) -> None:
    out = run_remote(ssh_host, brew_prefix,
                     _asuser(uid, user, "launchctl managername")).stdout
    if not is_aqua(out):
        raise RuntimeError(f"refusing to run: managername={out.strip()!r}, need Aqua")


def launch(ssh_host: str, brew_prefix: str, uid: int, user: str, build_dir: str,
           conf_path: str, log_file: str) -> None:
    """Launch lumen as a per-user LaunchAgent so launchd execs it directly (clean
    TCC attribution), inside the console Aqua session."""
    assert_aqua(ssh_host, brew_prefix, uid, user)
    plist = render_plist([f"{build_dir}/lumen", conf_path],
                         {"SUNSHINE_ASSETS_DIR": f"{build_dir}/assets"}, log_file)
    path = agent_plist_path(user)
    run_remote(ssh_host, brew_prefix,
               f"mkdir -p /Users/{user}/Library/LaunchAgents && "
               f"cat > {path} <<'PLIST'\n{plist}\nPLIST")
    # (re)bootstrap into the gui session (bootout any stale instance first)
    run_remote(ssh_host, brew_prefix, f"sudo -n launchctl bootout gui/{uid}/{PLIST_LABEL}",
               check=False)
    run_remote(ssh_host, brew_prefix, f"sudo -n launchctl bootstrap gui/{uid} {path}")
    # keep the session awake (separate process; does not affect lumen's TCC attribution)
    run_remote(ssh_host, brew_prefix,
               _asuser(uid, user, "bash -lc 'nohup caffeinate -dimsu >/dev/null 2>&1 &'"),
               check=False)


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
    raise TimeoutError("lumen did not reach ready state")


def teardown(ssh_host: str, brew_prefix: str, uid: int, user: str) -> None:
    run_remote(ssh_host, brew_prefix, f"sudo -n launchctl bootout gui/{uid}/{PLIST_LABEL}",
               check=False)
    run_remote(ssh_host, brew_prefix, _asuser(uid, user, "pkill -x caffeinate"), check=False)
