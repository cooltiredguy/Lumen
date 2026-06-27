"""
CLI wrapper for the instrumented moonlight-qt client.
Exposes three functions: pair(), stream(), quit_stream().
"""
import os
import subprocess
import time
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def pair(
    host: str,
    moonlight_bin: str,
    lumen_url: str,
    admin_user: str,
    admin_password: str,
    pin: str = "7777",
    timeout: int = 30,
) -> None:
    """
    Pair moonlight with a Lumen host using a fixed numeric code.

    Steps:
      1. Launch 'moonlight pair <host> --pin <pin>' in the background.
      2. Sleep 2s to give Moonlight time to connect and register the request.
      3. POST {"pin": pin, "name": "lumen-harness"} to <lumen_url>/api/pin.
      4. Wait for the Moonlight subprocess to exit (success = exit 0).
    """
    cmd = [moonlight_bin, "pair", host, "--pin", pin]
    print(f"[run.py] pair: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    time.sleep(2)  # give client time to connect to Lumen

    url = f"{lumen_url}/api/pin"
    resp = requests.post(
        url,
        json={"pin": pin, "name": "lumen-harness"},
        auth=(admin_user, admin_password),
        verify=False,
        timeout=10,
    )
    print(f"[run.py] POST {url} → {resp.status_code} {resp.text}")
    if not resp.ok:
        proc.kill()
        raise RuntimeError(f"POST /api/pin failed: {resp.status_code} {resp.text}")

    ret = proc.wait(timeout=timeout)
    if ret != 0:
        stderr = proc.stderr.read().decode(errors="replace")
        raise RuntimeError(f"moonlight pair exited {ret}: {stderr}")
    print("[run.py] pair complete")


def stream(
    host: str,
    moonlight_bin: str,
    app: str,
    resolution: str,
    fps: int,
    bitrate_kbps: int,
    stream_seconds: int,
    trace_file: str,
    run_id: str,
    topology: str,
    display_mode: str = "windowed",
    timeout: int = 120,
) -> None:
    """
    Stream <app> from <host> for stream_seconds, writing client trace to trace_file.

    moonlight stream <host> <app> --resolution <WxH> --fps <N> --bitrate <N>
        --display-mode windowed --no-vsync --no-frame-pacing
    """
    cmd = [
        moonlight_bin, "stream", host, app,
        "--resolution", resolution,
        "--fps", str(fps),
        "--bitrate", str(bitrate_kbps),
        "--display-mode", display_mode,
        "--no-vsync",
        "--no-frame-pacing",
    ]
    env = os.environ.copy()
    env["MOONLIGHT_TRACE_FILE"]     = trace_file
    env["MOONLIGHT_TRACE_RUN_ID"]   = run_id
    env["MOONLIGHT_TRACE_TOPOLOGY"] = topology

    print(f"[run.py] stream: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        env=env,
        timeout=stream_seconds + timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"moonlight stream exited {result.returncode}")
    print(f"[run.py] stream complete; trace at {trace_file}")


def quit_stream(host: str, moonlight_bin: str, timeout: int = 15) -> None:
    """Quit the currently running stream on host."""
    cmd = [moonlight_bin, "quit", host]
    print(f"[run.py] quit: {' '.join(cmd)}")
    result = subprocess.run(cmd, timeout=timeout)
    if result.returncode != 0:
        print(f"[run.py] WARNING: moonlight quit exited {result.returncode} (stream may have already ended)")
