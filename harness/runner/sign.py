from .mini import run_remote

KEYCHAIN = "lumen.keychain"
KEYCHAIN_PASS = "lumen"


def sign_binaries(ssh_host: str, brew_prefix: str, build_dir: str, identity: str) -> None:
    # Unlock + apply the partition list + codesign MUST happen in one SSH session:
    # over SSH the keychain ACL access is bound to the session that unlocked it
    # (separate sessions => errSecInteractionNotAllowed). Stable cert identity so
    # the TCC (Screen Recording) grant survives rebuilds.
    bins = " ".join(f"{build_dir}/{b}" for b in ("sunshine", "vd_helper"))
    cmd = (
        f"security unlock-keychain -p {KEYCHAIN_PASS} {KEYCHAIN} && "
        f"security set-key-partition-list -S apple-tool:,apple:,unsigned: "
        f"-s -k {KEYCHAIN_PASS} {KEYCHAIN} >/dev/null 2>&1; "
        f"for b in {bins}; do "
        f"codesign --force --sign '{identity}' --keychain {KEYCHAIN} "
        f'--timestamp=none "$b" || exit 1; done'
    )
    run_remote(ssh_host, brew_prefix, cmd)


def signed_identity(ssh_host: str, brew_prefix: str, build_dir: str) -> str:
    # Authority= only prints at verbose=4
    out = run_remote(ssh_host, brew_prefix,
                     f"codesign -d --verbose=4 {build_dir}/sunshine 2>&1 "
                     f"| grep -i 'Authority=' | head -1",
                     check=False).stdout
    return out.strip()
