import time
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

    print("[1/8] preconditions")
    uid = pre.console_uid(host, bp)
    user = pre.console_user_name(host, bp)
    assert pre.console_user_present(host, bp), "no console user logged in"
    assert pre.aqua_session_ready(host, bp, uid), "no Aqua session"
    print("[2/8] deploy"); mini.rsync_deploy(REPO, host, deploy)
    print("[3/8] deps"); assert deps.ensure_deps(host, bp) == [], "deps still missing"
    print("[4/8] build"); B.build(host, bp, deploy, bdir)
    print("[5/8] sign"); sign.sign_binaries(host, bp, bdir, ident)
    print("[6/8] config")
    conf = config_render.render_sunshine_conf(mll)
    mini.run_remote(host, bp, f"cat > {cdir}/harness.conf <<'EOF'\n{conf}\nEOF")
    mini.run_remote(host, bp, f": > {remote_log}")  # truncate
    try:
        power.disable_sleep_lock(host, bp)
        print("[7/8] launch + gate")
        session.launch(host, bp, uid, user, bdir, f"{cdir}/harness.conf", remote_log)
        session.wait_ready(host, bp, remote_log, timeout=90)
        print(f"      ready. idling {idle}s for log capture")
        time.sleep(idle)
    finally:
        print("[8/8] teardown")
        session.teardown(host, bp, uid, user)
        power.restore_sleep_lock(host, bp)
        local_log = rundir / "sunshine.log"
        out = mini.run_remote(host, bp, f"cat {remote_log}", check=False).stdout
        local_log.write_text(out)
        print(f"log saved: {local_log}  ({len(out.splitlines())} lines)")


if __name__ == "__main__":
    run()
