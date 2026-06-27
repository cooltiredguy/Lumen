"""
Per-topology run orchestration: starts the client (and readback + workload
when available), streams for N seconds, stops everything, and returns the
path to the collected client trace file.
"""
import os
import subprocess
import time
from pathlib import Path


def run_wifi_topology(cfg: dict, run_id: str, run_dir: Path) -> str:
    """
    Wi-Fi topology: client runs on the dev box (M5 Max) pointing at mac-mini.
    Returns path to the local client trace file.
    """
    client_cfg = cfg["client"]
    moonlight_bin = client_cfg["moonlight_bin_dev"]
    trace_file = str(run_dir / "client_wifi.jsonl")

    env = os.environ.copy()
    env["MOONLIGHT_TRACE_FILE"]     = trace_file
    env["MOONLIGHT_TRACE_RUN_ID"]   = run_id
    env["MOONLIGHT_TRACE_TOPOLOGY"] = "wifi"

    target = cfg.get("topologies", {}).get("wifi", {}).get("client_target", "mac-mini")
    cmd = [
        moonlight_bin, "stream", target,
        client_cfg["app"],
        "--resolution", client_cfg["resolution"],
        "--fps",        str(client_cfg["fps"]),
        "--bitrate",    str(client_cfg["bitrate_kbps"]),
        "--display-mode", "windowed",
        "--no-vsync",
        "--no-frame-pacing",
    ]

    print(f"[topology:wifi] starting stream: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, env=env)

    # Wait for Moonlight to open its window before starting readback
    _READBACK_DELAY = 5
    time.sleep(_READBACK_DELAY)

    # ── Start readback on dev box after Moonlight window is up ────────────
    readback_trace_local = str(run_dir / "readback_wifi.jsonl")
    readback_log_local   = str(run_dir / "readback_wifi.log")
    env_rb = os.environ.copy()
    env_rb["LUMEN_READBACK_TRACE_FILE"] = readback_trace_local
    env_rb["LUMEN_READBACK_BITS"]       = str(cfg["workload"]["counter_bits"])
    env_rb["LUMEN_READBACK_SECONDS"]    = str(client_cfg["stream_seconds"])
    readback_bin_local = str(Path(__file__).parent.parent / "readback" / "LumenReadback")
    with open(readback_log_local, "w") as log_fh:
        rb_proc = subprocess.Popen([readback_bin_local], env=env_rb,
                                   stdout=log_fh, stderr=log_fh)

    time.sleep(client_cfg["stream_seconds"])
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    try:
        rb_proc.terminate()
        rb_proc.wait(timeout=5)
    except Exception:
        rb_proc.kill()

    rb_events = Path(readback_trace_local).read_text().count("\n") if Path(readback_trace_local).exists() else 0
    rb_log = Path(readback_log_local).read_text().strip() if Path(readback_log_local).exists() else ""
    print(f"[topology:wifi] readback={rb_events} events")
    if rb_events == 0:
        print(f"[topology:wifi] readback log: {rb_log}")

    print(f"[topology:wifi] done; trace: {trace_file}")
    return trace_file


def run_loopback_topology(
    cfg: dict, run_id: str, run_dir: Path, ssh_host: str, brew_prefix: str,
    console_user: str = "hazemeissa"
) -> str:
    """
    Loopback topology: client runs on the mini against 127.0.0.1.
    Returns path to the local client trace file (fetched from mini after run).
    """
    from harness.runner import mini as minimod
    from harness.runner.launch_agent import (
        READBACK_LABEL, readback_plist_path, render_plist,
    )

    client_cfg = cfg["client"]
    moonlight_bin_mini = client_cfg["moonlight_bin_mini"]
    remote_trace = f"/tmp/client_loopback_{run_id}.jsonl"
    local_trace  = str(run_dir / "client_loopback.jsonl")

    # ── Start Moonlight stream FIRST (VD is created when client connects) ──
    # We need Moonlight to connect before we can get the VD ID.
    stream_cmd = (
        f"sudo -n launchctl asuser 501 sudo -u {console_user} env "
        f"MOONLIGHT_TRACE_FILE={remote_trace} "
        f"MOONLIGHT_TRACE_RUN_ID={run_id} "
        f"MOONLIGHT_TRACE_TOPOLOGY=loopback "
        f"{moonlight_bin_mini} stream 127.0.0.1 {client_cfg['app']} "
        f"--resolution {client_cfg['resolution']} "
        f"--fps {client_cfg['fps']} "
        f"--bitrate {client_cfg['bitrate_kbps']} "
        # borderless: Moonlight fills the physical display in the current Space
        # without creating a new macOS fullscreen Space. This keeps the display on the
        # desktop so SCK captures Moonlight's content (not a separate Space's wallpaper).
        f"--display-mode borderless --no-vsync --no-frame-pacing"
    )
    print(f"[topology:loopback] starting client on mini")
    subprocess.Popen(["ssh", ssh_host, stream_cmd + " &"])

    # ── Poll for VD ID (Sunshine creates VD when Moonlight connects) ──────
    vd_id = ""
    for _ in range(20):  # up to 10 s
        time.sleep(0.5)
        r = minimod.run_remote(ssh_host, brew_prefix,
            "cat /tmp/sunshine_vd_id 2>/dev/null", check=False)
        vd_id = r.stdout.strip()
        if vd_id:
            break
    print(f"[topology:loopback] VD ID: {vd_id!r}")

    # ── Start workload on the virtual display ─────────────────────────────
    workload_bin = "/Volumes/T7/lumen-harness/harness-tools/LumenWorkload"
    workload_trace = f"/tmp/workload_{run_id}.jsonl"
    workload_cmd = (
        f"sudo -n launchctl asuser 501 sudo -u {console_user} env "
        f"LUMEN_VIRTUAL_DISPLAY_ID={vd_id} "
        f"LUMEN_WORKLOAD_TRACE_FILE={workload_trace} "
        f"{workload_bin} {cfg['workload']['fps']} "
        f"{cfg['workload']['counter_bits']} "
        f"{client_cfg['stream_seconds']}"
    )
    subprocess.Popen(["ssh", ssh_host, workload_cmd + " &"])

    # ── Start readback via LaunchAgent (launchd direct exec = correct TCC attribution) ──
    # Delay so Moonlight window is up before we search for it
    _READBACK_DELAY = 5
    time.sleep(_READBACK_DELAY)

    readback_bin = "/Volumes/T7/lumen-harness/harness-tools/LumenReadback"
    readback_trace = f"/tmp/readback_loopback_{run_id}.jsonl"
    readback_log = f"/tmp/readback_loopback_{run_id}.log"
    rb_plist = render_plist(
        [readback_bin],
        {
            "LUMEN_READBACK_TRACE_FILE": readback_trace,
            "LUMEN_READBACK_BITS":       str(cfg["workload"]["counter_bits"]),
            "LUMEN_READBACK_SECONDS":    str(client_cfg["stream_seconds"]),
        },
        readback_log,
        label=READBACK_LABEL,
    )
    rb_path = readback_plist_path(console_user)
    minimod.run_remote(ssh_host, brew_prefix,
        f"mkdir -p /Users/{console_user}/Library/LaunchAgents && "
        f"cat > {rb_path} <<'PLIST'\n{rb_plist}\nPLIST")
    # bootout any stale instance, then bootstrap fresh
    minimod.run_remote(ssh_host, brew_prefix,
        f"sudo -n launchctl bootout gui/501/{READBACK_LABEL}", check=False)
    minimod.run_remote(ssh_host, brew_prefix,
        f"sudo -n launchctl bootstrap gui/501 {rb_path}")

    # Wait for readback to finish (it self-terminates after LUMEN_READBACK_SECONDS)
    time.sleep(client_cfg["stream_seconds"] + 2)

    minimod.run_remote(ssh_host, brew_prefix,
        f"sudo -n launchctl bootout gui/501/{READBACK_LABEL}", check=False)

    # Kill the Moonlight client on the mini
    minimod.run_remote(ssh_host, brew_prefix,
        f"pkill -u {console_user} -f 'Moonlight.*127.0.0.1' || true", check=False)
    time.sleep(2)

    # Fetch client trace back to dev box
    result = minimod.run_remote(ssh_host, brew_prefix,
        f"cat {remote_trace} 2>/dev/null", check=False)
    raw = result.stdout
    Path(local_trace).write_text(raw)
    event_count = len([l for l in raw.splitlines() if l.strip()])
    print(f"[topology:loopback] trace fetched: {local_trace} ({event_count} events)")

    # Fetch workload trace
    wt_result = minimod.run_remote(ssh_host, brew_prefix,
        f"cat {workload_trace} 2>/dev/null", check=False)
    wt_raw = wt_result.stdout
    (run_dir / "workload_trace.jsonl").write_text(wt_raw)

    # Fetch loopback readback trace and log
    rb_result = minimod.run_remote(ssh_host, brew_prefix,
        f"cat {readback_trace} 2>/dev/null", check=False)
    rb_raw = rb_result.stdout
    (run_dir / "readback_loopback.jsonl").write_text(rb_raw)
    rb_log_result = minimod.run_remote(ssh_host, brew_prefix,
        f"cat {readback_log} 2>/dev/null", check=False)
    (run_dir / "readback_loopback.log").write_text(rb_log_result.stdout)
    rb_events = rb_raw.count("\n")
    print(f"[topology:loopback] workload={wt_raw.count(chr(10))} readback={rb_events} events")
    if rb_events == 0:
        print(f"[topology:loopback] readback log: {rb_log_result.stdout.strip()}")

    return local_trace
