import time
from pathlib import Path
from .runctx import load_cfg, new_run_dir, REPO
from . import mini, preconditions as pre, deps, build as B, sign, config_render, session, power
from harness.trace.report import generate_report


def run():
    cfg = load_cfg()
    host = cfg["mini"]["ssh_host"]; bp = cfg["mini"]["brew_prefix"]
    deploy = cfg["mini"]["deploy_dir"]; bdir = cfg["mini"]["build_dir"]
    ident = cfg["signing"]["identity"]; cdir = cfg["run"]["config_dir"]
    mll = cfg["run"]["min_log_level"]; idle = cfg["run"]["idle_seconds"]
    trace_dir = cfg["run"]["trace_dir"]
    rundir = new_run_dir()
    run_id = rundir.name
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
    mini.run_remote(host, bp, f": > {remote_log}")
    mini.run_remote(host, bp, f"mkdir -p {trace_dir}")
    trace_remote = f"{trace_dir}/{run_id}.jsonl"
    client_traces: list = []
    try:
        power.disable_sleep_lock(host, bp)
        print("[7/8] launch + gate")
        session.launch(host, bp, uid, user, bdir, f"{cdir}/harness.conf", remote_log,
                       trace_file=trace_remote, run_id=run_id)
        session.wait_ready(host, bp, remote_log, timeout=90)
        print(f"      ready. idling {idle}s for log capture")
        # ─── [7.5/8] topology runs ──────────────────────────────────────────
        from harness.runner.topology import run_wifi_topology, run_loopback_topology
        topo_order = cfg.get("topologies", {}).get("order", [])
        if "wifi" in topo_order:
            wifi_trace = run_wifi_topology(cfg, run_id, rundir)
            client_traces.append(("wifi", wifi_trace))
        if "loopback" in topo_order:
            loop_trace = run_loopback_topology(cfg, run_id, rundir, host, bp)
            client_traces.append(("loopback", loop_trace))
    finally:
        print("[8/8] teardown")
        session.teardown(host, bp, uid, user)
        power.restore_sleep_lock(host, bp)
        local_log = rundir / "sunshine.log"
        out = mini.run_remote(host, bp, f"cat {remote_log}", check=False).stdout
        local_log.write_text(out)
        print(f"log saved: {local_log}  ({len(out.splitlines())} lines)")
        # fetch trace and generate report
        trace_out = mini.run_remote(host, bp, f"cat {trace_remote} 2>/dev/null", check=False).stdout
        local_trace = rundir / "host_trace.jsonl"
        local_trace.write_text(trace_out)
        event_count = len([l for l in trace_out.splitlines() if l.strip()])
        print(f"trace saved: {local_trace}  ({event_count} events)")
        # merge host + all client traces
        all_events_jsonl = trace_out  # host trace text already fetched above
        for _topo, ct in client_traces:
            ct_path = Path(ct)
            if ct_path.exists():
                all_events_jsonl += ct_path.read_text()
        merged_trace = rundir / "merged_trace.jsonl"
        merged_trace.write_text(all_events_jsonl)
        merged_count = len([l for l in all_events_jsonl.splitlines() if l.strip()])
        print(f"merged trace: {merged_trace}  ({merged_count} events)")
        if merged_count > 0:
            generate_report(str(merged_trace), rundir)
            print(f"report: {rundir}/report.md")
        else:
            print("WARNING: no trace events — check env vars in LaunchAgent plist")


if __name__ == "__main__":
    run()
