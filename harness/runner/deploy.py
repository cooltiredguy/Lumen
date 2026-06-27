"""
Deploy pre-built harness artifacts (client, workload, readback) to the mini.

These are NOT included in the main rsync (harness/ is excluded from the
primary deploy to keep the Lumen source sync fast). We deploy only build
outputs: the Moonlight binary bundle and the Swift tool binaries.
"""
import subprocess
from pathlib import Path


def rsync_to_mini(local_path: str, remote_path: str, ssh_host: str) -> None:
    """rsync a file or directory to ssh_host:remote_path."""
    # Create remote parent dir first; --mkpath is not available on macOS rsync
    remote_dir = remote_path if remote_path.endswith("/") else str(Path(remote_path).parent)
    subprocess.run(["ssh", ssh_host, f"mkdir -p {remote_dir}"], check=True)
    cmd = [
        "rsync", "-avz",
        local_path,
        f"{ssh_host}:{remote_path}",
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    print(f"[deploy] rsync → {remote_path}: OK ({result.stdout.count('>')} files)")


def deploy_client(cfg: dict, ssh_host: str) -> str:
    """
    Push the Moonlight.app bundle to the mini.

    The bundle is built from harness/client/setup.sh run on the dev box.
    After deploy, the mini can launch the binary for loopback runs.
    Returns the path to the Moonlight binary on the mini.
    """
    local_app = cfg["client"]["moonlight_bin_dev"].replace(
        "/Contents/MacOS/Moonlight", ""
    )  # → .../Moonlight.app
    remote_dir = "/Volumes/T7/lumen-harness/moonlight-qt-mini/"
    print(f"[deploy] deploying Moonlight.app → mini:{remote_dir}")
    rsync_to_mini(local_app, remote_dir, ssh_host)
    mini_bin = remote_dir + "Moonlight.app/Contents/MacOS/Moonlight"
    print(f"[deploy] mini moonlight_bin: {mini_bin}")
    return mini_bin


def deploy_workload(cfg: dict, ssh_host: str) -> str:
    """Push the LumenWorkload binary to the mini."""
    local_bin = str(Path(__file__).parent.parent / "workload" / "LumenWorkload")
    remote_dir = "/Volumes/T7/lumen-harness/harness-tools/"
    rsync_to_mini(local_bin, remote_dir + "LumenWorkload", ssh_host)
    return remote_dir + "LumenWorkload"


def deploy_readback(cfg: dict, ssh_host: str) -> str:
    """Push the LumenReadback binary to the mini."""
    local_bin = str(Path(__file__).parent.parent / "readback" / "LumenReadback")
    remote_dir = "/Volumes/T7/lumen-harness/harness-tools/"
    rsync_to_mini(local_bin, remote_dir + "LumenReadback", ssh_host)
    return remote_dir + "LumenReadback"
