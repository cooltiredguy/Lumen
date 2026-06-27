import subprocess


def remote_cmd(brew_prefix: str, cmd: str) -> str:
    """Wrap a command so Homebrew is on PATH in a non-interactive SSH shell."""
    return f'eval "$({brew_prefix}/bin/brew shellenv)"; {cmd}'


def run_remote(ssh_host: str, brew_prefix: str, cmd: str, check: bool = True,
               timeout: int = 1800) -> subprocess.CompletedProcess:
    wrapped = remote_cmd(brew_prefix, cmd)
    return subprocess.run(["ssh", ssh_host, wrapped], capture_output=True,
                          text=True, check=check, timeout=timeout)


def run_remote_stream(ssh_host: str, brew_prefix: str, cmd: str) -> subprocess.Popen:
    """Run a remote command, streaming combined output to this process's stdout."""
    wrapped = remote_cmd(brew_prefix, cmd)
    return subprocess.Popen(["ssh", ssh_host, wrapped],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def rsync_excludes() -> list[str]:
    return [".git/", "build/", "harness/reports/", "third-party/*/build/",
            "*.log", "__pycache__/", ".DS_Store"]


def rsync_deploy(local_dir: str, ssh_host: str, deploy_dir: str) -> subprocess.CompletedProcess:
    args = ["rsync", "-az", "--delete"]
    for ex in rsync_excludes():
        args += ["--exclude", ex]
    # ensure remote parent exists; trailing slash copies contents into deploy_dir
    subprocess.run(["ssh", ssh_host, f"mkdir -p {deploy_dir}"], check=True)
    args += [f"{local_dir.rstrip('/')}/", f"{ssh_host}:{deploy_dir}/"]
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=1800)
