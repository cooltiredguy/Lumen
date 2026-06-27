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

    # ── Start readback on dev box (captures Moonlight window) ────────────
    readback_trace_local = str(run_dir / "readback_wifi.jsonl")
    env_rb = os.environ.copy()
    env_rb["LUMEN_READBACK_TRACE_FILE"] = readback_trace_local
    env_rb["LUMEN_READBACK_BITS"]       = str(cfg["workload"]["counter_bits"])
    env_rb["LUMEN_READBACK_SECONDS"]    = str(client_cfg["stream_seconds"])
    readback_bin_local = str(Path(__file__).parent.parent / "readback" / "LumenReadback")
    rb_proc = subprocess.Popen([readback_bin_local], env=env_rb)

    print(f"[topology:wifi] starting stream: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, env=env)
    time.sleep(client_cfg["stream_seconds"])
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    # Stop readback
    try:
        rb_proc.terminate()
        rb_proc.wait(timeout=5)
    except Exception:
        rb_proc.kill()

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

    client_cfg = cfg["client"]
    moonlight_bin_mini = client_cfg["moonlight_bin_mini"]
    remote_trace = f"/tmp/client_loopback_{run_id}.jsonl"
    local_trace  = str(run_dir / "client_loopback.jsonl")

    # Get virtual display ID (written by Lumen to /tmp/sunshine_vd_id on mini)
    vd_result = minimod.run_remote(ssh_host, brew_prefix,
        "cat /tmp/sunshine_vd_id 2>/dev/null", check=False)
    vd_id = vd_result.stdout.strip()

    workload_bin = "/Volumes/T7/lumen-harness/harness-tools/LumenWorkload"
    workload_trace = f"/tmp/workload_{run_id}.jsonl"
    workload_cmd = (
        f"sudo -n launchctl asuser 501 env "
        f"LUMEN_VIRTUAL_DISPLAY_ID={vd_id} "
        f"LUMEN_WORKLOAD_TRACE_FILE={workload_trace} "
        f"{workload_bin} {cfg['workload']['fps']} "
        f"{cfg['workload']['counter_bits']} "
        f"{client_cfg['stream_seconds']}"
    )
    subprocess.Popen(["ssh", ssh_host, workload_cmd + " &"])

    readback_bin = "/Volumes/T7/lumen-harness/harness-tools/LumenReadback"
    readback_trace = f"/tmp/readback_loopback_{run_id}.jsonl"
    readback_cmd = (
        f"sudo -n launchctl asuser 501 env "
        f"LUMEN_READBACK_TRACE_FILE={readback_trace} "
        f"LUMEN_READBACK_BITS={cfg['workload']['counter_bits']} "
        f"LUMEN_READBACK_SECONDS={client_cfg['stream_seconds']} "
        f"{readback_bin}"
    )
    subprocess.Popen(["ssh", ssh_host, readback_cmd + " &"])

    stream_cmd = (
        f"sudo -n launchctl asuser 501 sudo -u {console_user} env "
        f"MOONLIGHT_TRACE_FILE={remote_trace} "
        f"MOONLIGHT_TRACE_RUN_ID={run_id} "
        f"MOONLIGHT_TRACE_TOPOLOGY=loopback "
        f"{moonlight_bin_mini} stream 127.0.0.1 {client_cfg['app']} "
        f"--resolution {client_cfg['resolution']} "
        f"--fps {client_cfg['fps']} "
        f"--bitrate {client_cfg['bitrate_kbps']} "
        f"--display-mode windowed --no-vsync --no-frame-pacing"
    )
    print(f"[topology:loopback] starting client on mini")
    # Run in background — SSH exits immediately; moonlight stream continues on mini
    subprocess.Popen(["ssh", ssh_host, stream_cmd + " &"])
    time.sleep(client_cfg["stream_seconds"])

    # Kill the client on the mini (runs as console_user now, so no sudo needed)
    minimod.run_remote(ssh_host, brew_prefix,
        f"pkill -u {console_user} -f 'Moonlight.*127.0.0.1' || true", check=False)
    time.sleep(2)

    # Fetch trace back to dev box
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

    # Fetch loopback readback trace
    rb_result = minimod.run_remote(ssh_host, brew_prefix,
        f"cat {readback_trace} 2>/dev/null", check=False)
    rb_raw = rb_result.stdout
    (run_dir / "readback_loopback.jsonl").write_text(rb_raw)
    print(f"[topology:loopback] workload={wt_raw.count(chr(10))} readback={rb_raw.count(chr(10))} events")

    return local_trace
