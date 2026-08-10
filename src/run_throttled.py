"""
Resource-aware throttled runner for POISE Docker evaluation.

Before each submission, queries Daytona SDK for actual CPU/memory/storage
usage across all active sandboxes. Only submits a new task when remaining
resources can accommodate it.

Usage (from the release root):
    # Run one prepared matrix cell
    python src/run_throttled.py --sample samples/SB/poise.csv \
        --workdir workdir/SB-poise-codex --jobs-dir jobs/SB-poise-codex \
        --run-tag SB-poise-codex-trial1

    # Smoke-test the first two prepared variants
    python src/run_throttled.py --sample samples/SB/poise.csv \
        --workdir workdir/SB-poise-codex --limit-variants 2

    # Override the default resource ceilings
    python src/run_throttled.py --sample ... --limit-cpu 100 --limit-mem 200 --limit-disk 300
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Force unbuffered output so tee/terminal see prints immediately
os.environ["PYTHONUNBUFFERED"] = "1"

# Sandbox lifetime cap for explicitly run-tagged sandboxes created by this run.
MAX_SANDBOX_AGE_SEC = 3600  # 1 hour

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CANARY_ARTIFACT = "/opt/poise-canary"

# Default resource ceilings
DEFAULT_LIMIT_CPU = 100
DEFAULT_LIMIT_MEM = 200   # GiB
DEFAULT_LIMIT_DISK = 300  # GiB

# Reserve margin (don't fill to 100%)
MARGIN_CPU = 4
MARGIN_MEM = 8    # GiB
MARGIN_DISK = 20  # GiB


# ── Resource querying ──────────────────────────────────────────────────────

def get_resource_usage() -> dict:
    """Query Daytona for current resource usage across all active sandboxes."""
    try:
        from daytona_sdk import Daytona, DaytonaConfig
        d = Daytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"]))
        items = d.list().items

        active_states = {"SandboxState.STARTED", "SandboxState.STARTING",
                         "SandboxState.BUILDING_SNAPSHOT", "SandboxState.PULLING_SNAPSHOT"}

        used_cpu, used_mem, used_disk = 0, 0, 0
        active_count = 0
        for s in items:
            if str(s.state) in active_states:
                used_cpu += s.cpu
                used_mem += s.memory
                used_disk += s.disk
                active_count += 1

        return {"used_cpu": used_cpu, "used_mem": used_mem, "used_disk": used_disk,
                "active": active_count, "total_sandboxes": len(items), "ok": True}
    except Exception as e:
        print(f"  [warn] Daytona query failed: {e}", file=sys.stderr)
        return {"used_cpu": 0, "used_mem": 0, "used_disk": 0,
                "active": 0, "total_sandboxes": 0, "ok": False}


def read_task_resources(variant_dir: Path) -> dict:
    """Read CPU/memory/disk requirements from task.toml."""
    toml_path = variant_dir / "task.toml"
    if not toml_path.exists():
        return {"cpu": 4, "mem": 8, "disk": 10}

    content = toml_path.read_text()
    cpu_m = re.search(r"cpus\s*=\s*(\d+)", content)
    mem_m = re.search(r"memory_mb\s*=\s*(\d+)", content)
    disk_m = re.search(r"storage_mb\s*=\s*(\d+)", content)

    cpu = int(cpu_m.group(1)) if cpu_m else 1
    mem_mb = int(mem_m.group(1)) if mem_m else 4096
    disk_mb = int(disk_m.group(1)) if disk_m else 10240

    # Convert to GiB (ceiling)
    import math
    return {"cpu": cpu, "mem": math.ceil(mem_mb / 1024), "disk": math.ceil(disk_mb / 1024)}


# ── Watchdog: report stale run-tagged sandboxes; local timeout stops Harbor ──

_watchdog_stop = threading.Event()


def _sandbox_has_run_tag(sandbox, run_tag: str) -> bool:
    """Return true only for an exact provider label/metadata ownership tag."""
    for attr_name in ("labels", "metadata"):
        value = getattr(sandbox, attr_name, None)
        if not isinstance(value, dict):
            continue
        for key in ("poise_run_tag", "POISE_RUN_TAG"):
            if value.get(key) == run_tag:
                return True
    return False


def watchdog_loop(run_tag: str, interval_sec: int = 60):
    """Report stale sandboxes bearing this run's exact ownership tag."""
    try:
        from daytona_sdk import Daytona, DaytonaConfig
        d = Daytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"]))
    except Exception as e:
        print(f"  [watchdog] failed to init Daytona client: {e}", flush=True)
        return

    while not _watchdog_stop.is_set():
        try:
            items = d.list().items
            now = datetime.now(timezone.utc)
            killed = 0
            for s in items:
                if not _sandbox_has_run_tag(s, run_tag):
                    continue
                # created_at is a datetime; compute age in seconds
                try:
                    age = (now - s.created_at).total_seconds()
                except Exception:
                    continue
                if age <= MAX_SANDBOX_AGE_SEC:
                    continue
                # Harbor does not expose the created sandbox ID to this
                # launcher. Even an exact provider label is insufficient proof
                # that this process created the resource, so remote deletion is
                # deliberately disabled. The subprocess timeout stops Harbor;
                # provider cleanup remains an explicit operator action.
                print(
                    f"  [watchdog] tagged sandbox {s.id[:12]} exceeded "
                    f"{MAX_SANDBOX_AGE_SEC}s; remote deletion is disabled",
                    flush=True,
                )
            if killed:
                print(f"  [watchdog] killed {killed} stale sandboxes", flush=True)
        except Exception as e:
            print(f"  [watchdog] loop error: {str(e)[:120]}", flush=True)

        # Sleep but wake up early on stop signal
        _watchdog_stop.wait(interval_sec)


def start_watchdog(run_tag: str):
    t = threading.Thread(
        target=watchdog_loop,
        args=(run_tag,),
        daemon=True,
        name="watchdog",
    )
    t.start()
    return t


def can_fit(usage: dict, task_res: dict, limits: dict) -> bool:
    """Check if the task fits within remaining resources (with margin)."""
    avail_cpu = limits["cpu"] - usage["used_cpu"] - MARGIN_CPU
    avail_mem = limits["mem"] - usage["used_mem"] - MARGIN_MEM
    avail_disk = limits["disk"] - usage["used_disk"] - MARGIN_DISK

    return (task_res["cpu"] <= avail_cpu and
            task_res["mem"] <= avail_mem and
            task_res["disk"] <= avail_disk)


def wait_for_capacity(task_res: dict, limits: dict, poll_interval: int = 20) -> dict:
    """Block until resources are available for this task."""
    while True:
        usage = get_resource_usage()
        if not usage["ok"]:
            # Can't query — wait and retry
            time.sleep(poll_interval)
            continue

        if can_fit(usage, task_res, limits):
            return usage

        avail_cpu = limits["cpu"] - usage["used_cpu"]
        avail_mem = limits["mem"] - usage["used_mem"]
        avail_disk = limits["disk"] - usage["used_disk"]
        print(f"  [throttle] need cpu={task_res['cpu']} mem={task_res['mem']}G disk={task_res['disk']}G  |  "
              f"avail cpu={avail_cpu} mem={avail_mem}G disk={avail_disk}G  |  "
              f"active={usage['active']}  waiting {poll_interval}s...")
        time.sleep(poll_interval)


# ── Single variant execution ───────────────────────────────────────────────

def _cleanup_task_scratch(jobs_dir: Path, vid: str, run_tag: str) -> None:
    """Remove per-task runtime scratch after harbor completes.

    The Codex toolchain (codex-linux-sandbox, codex-execve-wrapper, applypatch,
    apply_patch — 4×179 MB = ~720 MB per task) lives under agent/tmp/arg0/codex-*/.
    None of it is read during harvest, which only touches config.json, verifier/,
    and artifacts/. Dropping agent/tmp/ reclaims ~99% of the per-task footprint.
    """
    jobs_root = jobs_dir.resolve()
    if run_tag not in jobs_root.parts:
        print(
            f"  [cleanup] refused untagged jobs directory: {jobs_root}",
            file=sys.stderr,
        )
        return
    for tmp_dir in jobs_root.rglob("tmp"):
        if tmp_dir.parent.name != "agent" or vid not in tmp_dir.parts:
            continue
        resolved = tmp_dir.resolve()
        if jobs_root not in resolved.parents:
            continue
        shutil.rmtree(resolved, ignore_errors=True)


def build_harbor_command(
    variant_dir: Path,
    jobs_dir: Path,
    *,
    agent: str,
    model: str,
    env: str,
    agent_kwargs: list[str] | None = None,
    agent_timeout_mult: float | None = None,
    agent_import_path: str | None = None,
) -> list[str]:
    """Build one Harbor command without mixing named and imported agents."""
    base_cmd = [
        "harbor", "run",
        "-p", str(variant_dir),
    ]
    if agent_import_path:
        base_cmd.extend(["--agent-import-path", agent_import_path])
    else:
        base_cmd.extend(["-a", agent])
    base_cmd.extend([
        "-m", model,
        "--env", env,
        "-n", "1",
        "--artifact", CANARY_ARTIFACT,
        "-o", str(jobs_dir),
        "-y", "-q",
    ])
    for kv in (agent_kwargs or []):
        base_cmd.extend(["--agent-kwarg", kv])
    if agent_timeout_mult is not None:
        base_cmd.extend(["--agent-timeout-multiplier", str(agent_timeout_mult)])
    return base_cmd


def run_one_variant(variant_dir: Path, jobs_dir: Path, agent: str,
                    model: str, env: str, max_retries: int = 2,
                    agent_kwargs: list[str] | None = None,
                    agent_timeout_mult: float | None = None,
                    run_tag: str = "",
                    agent_import_path: str | None = None) -> dict:
    """Run a single variant via Harbor, with one build-failure retry by default.

    A retry forces a rebuild and, when needed, requests the per-sandbox maximum
    of 8 GiB memory and 10 GiB storage.
    """
    vid = variant_dir.name
    base_cmd = build_harbor_command(
        variant_dir,
        jobs_dir,
        agent=agent,
        model=model,
        env=env,
        agent_kwargs=agent_kwargs,
        agent_timeout_mult=agent_timeout_mult,
        agent_import_path=agent_import_path,
    )

    # Per-sandbox max: 4 vCPU, 8 GiB mem, 10 GiB disk
    MAX_CPU = 4
    MAX_MEM_MB = 8192
    MAX_DISK_MB = 10240

    task_res = read_task_resources(variant_dir)
    base_mem_mb = task_res["mem"] * 1024
    base_disk_mb = task_res["disk"] * 1024

    try:
        for attempt in range(1, max_retries + 1):
            cmd = list(base_cmd)
            if attempt > 1:
                # Force rebuild (bypass cached failure) + max per-sandbox limits
                cmd += ["--force-build"]
                if base_mem_mb < MAX_MEM_MB or base_disk_mb < MAX_DISK_MB:
                    cmd += ["--override-memory", str(MAX_MEM_MB),
                            "--override-storage", str(MAX_DISK_MB)]

            t0 = time.time()
            try:
                # Stop the local Harbor process five minutes after the monitored
                # age threshold; remote sandbox deletion is deliberately disabled.
                child_env = os.environ.copy()
                child_env["POISE_RUN_TAG"] = run_tag
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=MAX_SANDBOX_AGE_SEC + 300,
                    cwd=str(PROJECT_ROOT.parent),
                    env=child_env,
                )
                elapsed = time.time() - t0
                stderr = proc.stderr[-500:] if proc.stderr else ""

                is_build_fail = ("BUILD_FAILED" in stderr or "code: 137" in stderr
                                 or "DaytonaAuthorizationError" in stderr
                                 or "EnvironmentStartTimeout" in stderr)

                if proc.returncode != 0 and is_build_fail and attempt < max_retries:
                    wait = 30 * attempt
                    print(f"  [retry] {vid[:50]} attempt {attempt} failed, "
                          f"retrying with max resources (8G/10G) in {wait}s...")
                    time.sleep(wait)
                    continue

                return {"vid": vid, "returncode": proc.returncode, "elapsed": elapsed,
                        "attempt": attempt,
                        "stdout": proc.stdout[-500:] if proc.stdout else "",
                        "stderr": stderr}
            except subprocess.TimeoutExpired:
                return {"vid": vid, "returncode": -1, "elapsed": time.time() - t0,
                        "attempt": attempt,
                        "stdout": "", "stderr": f"harbor timeout ({MAX_SANDBOX_AGE_SEC + 300}s)"}
            except Exception as e:
                return {"vid": vid, "returncode": -1, "elapsed": time.time() - t0,
                        "attempt": attempt,
                        "stdout": "", "stderr": str(e)}

        return {"vid": vid, "returncode": -1, "elapsed": 0,
                "attempt": max_retries,
                "stdout": "", "stderr": "all retries exhausted"}
    finally:
        _cleanup_task_scratch(jobs_dir, vid, run_tag)


# ── Main orchestrator ──────────────────────────────────────────────────────

def run_all(workdir: Path, jobs_dir: Path, limits: dict,
            agent: str, model: str, env: str, max_workers: int,
            agent_kwargs: list[str] | None = None,
            limit_variants: int | None = None,
            agent_timeout_mult: float | None = None,
            run_tag: str = "",
            agent_import_path: str | None = None):
    """Submit all variants with resource-aware throttling."""
    variant_dirs = sorted([d for d in workdir.iterdir()
                           if d.is_dir() and (d / "task.toml").exists()])
    if limit_variants:
        variant_dirs = variant_dirs[:limit_variants]
    total = len(variant_dirs)
    print(f"\nSubmitting {total} variants")
    print(f"Limits: cpu={limits['cpu']}  mem={limits['mem']}G  disk={limits['disk']}G")
    print(f"Margin: cpu={MARGIN_CPU}  mem={MARGIN_MEM}G  disk={MARGIN_DISK}G")
    print(f"Max concurrent harbor processes: {max_workers}\n")

    jobs_dir.mkdir(parents=True, exist_ok=True)

    # Pre-read all task resource requirements
    task_resources = {}
    for vdir in variant_dirs:
        task_resources[vdir.name] = read_task_resources(vdir)

    completed = 0
    succeeded = 0
    failed = 0
    submitted = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        pending = {}  # future -> vid
        queue = list(enumerate(variant_dirs))  # (index, vdir) pairs

        def drain_completed():
            """Check for and report any completed futures (non-blocking)."""
            nonlocal completed, succeeded, failed
            done_futures = [f for f in pending if f.done()]
            for future in done_futures:
                vid = pending.pop(future)
                result = future.result()
                completed += 1
                rc = result["returncode"]
                elapsed = result["elapsed"]
                attempt = result.get("attempt", 1)
                retry_info = f" (attempt {attempt})" if attempt > 1 else ""

                if rc == 0:
                    succeeded += 1
                    print(f"  [done {completed}/{total}] OK  {vid[:50]}  "
                          f"({elapsed:.0f}s){retry_info}")
                else:
                    failed += 1
                    err = result["stderr"][:80] if result["stderr"] else ""
                    print(f"  [done {completed}/{total}] FAIL(rc={rc})  "
                          f"{vid[:50]}  {err}{retry_info}")

        while queue or pending:
            # Report any finished tasks
            drain_completed()

            # Try to submit next task if queue is not empty
            if queue:
                idx, vdir = queue[0]
                res = task_resources[vdir.name]
                usage = get_resource_usage()

                if usage["ok"] and can_fit(usage, res, limits) and len(pending) < max_workers:
                    queue.pop(0)
                    submitted += 1

                    avail_cpu = limits["cpu"] - usage["used_cpu"]
                    avail_mem = limits["mem"] - usage["used_mem"]
                    avail_disk = limits["disk"] - usage["used_disk"]

                    print(f"  [{submitted}/{total}] submit {vdir.name[:55]}  "
                          f"(need {res['cpu']}c/{res['mem']}g/{res['disk']}g  "
                          f"avail {avail_cpu}c/{avail_mem}g/{avail_disk}g  "
                          f"active={usage['active']}  inflight={len(pending)})")

                    future = pool.submit(run_one_variant, vdir, jobs_dir,
                                         agent, model, env, 2, agent_kwargs,
                                         agent_timeout_mult, run_tag,
                                         agent_import_path)
                    pending[future] = vdir.name

                    time.sleep(3)  # brief pause between submissions
                    continue
                else:
                    # Can't submit yet — either no capacity or too many inflight
                    if usage["ok"]:
                        avail_cpu = limits["cpu"] - usage["used_cpu"]
                        avail_mem = limits["mem"] - usage["used_mem"]
                        avail_disk = limits["disk"] - usage["used_disk"]
                        print(f"  [throttle] need {res['cpu']}c/{res['mem']}g/{res['disk']}g  "
                              f"avail {avail_cpu}c/{avail_mem}g/{avail_disk}g  "
                              f"active={usage['active']}  inflight={len(pending)}  "
                              f"queue={len(queue)}  waiting 20s...")
                    time.sleep(20)
            else:
                # Queue empty, just wait for remaining to finish
                time.sleep(10)

        # Final drain
        drain_completed()

    print(f"\n{'='*60}")
    print(f"Finished: {completed} total, {succeeded} succeeded, {failed} failed")
    print(f"{'='*60}")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser(description="Resource-aware throttled POISE runner")
    ap.add_argument("--sample", required=True, help="Sample CSV (for harvest)")
    ap.add_argument("--workdir", required=True,
                    help="Variant workdir (already prepared, relative to release root)")
    ap.add_argument("--jobs-dir", default="jobs/throttled",
                    help="Harbor jobs output root (relative to release root)")
    ap.add_argument("--output", default=None, help="Results CSV output path")
    ap.add_argument("--limit-cpu", type=int, default=DEFAULT_LIMIT_CPU)
    ap.add_argument("--limit-mem", type=int, default=DEFAULT_LIMIT_MEM,
                    help="Total memory limit in GiB")
    ap.add_argument("--limit-disk", type=int, default=DEFAULT_LIMIT_DISK,
                    help="Total disk limit in GiB")
    ap.add_argument("--max-workers", type=int, default=15,
                    help="Max concurrent harbor subprocesses")
    ap.add_argument("--agent", default="codex")
    ap.add_argument(
        "--agent-import-path",
        default=None,
        metavar="MODULE:CLASS",
        help=(
            "Load a vendored/custom Harbor agent by import path. When set, "
            "--agent is retained only as the result label."
        ),
    )
    ap.add_argument("--model", default="openai/gpt-5.2")
    ap.add_argument("--env", default="daytona")
    ap.add_argument(
        "--run-tag",
        default=os.environ.get("POISE_RUN_TAG", ""),
        help=(
            "Filesystem-safe ownership tag. A unique tag is generated when "
            "omitted; outputs are placed below a directory with this exact tag."
        ),
    )
    ap.add_argument("--harvest-only", action="store_true")
    ap.add_argument("--agent-kwarg", action="append", default=[],
                    metavar="KEY=VALUE",
                    help="Forward agent kwarg to harbor (key=value, repeatable). "
                         "E.g. version=2.1.146 to pin claude-code at install time.")
    ap.add_argument("--limit-variants", type=int, default=None,
                    help="Run only the first N variants (smoke testing).")
    ap.add_argument("--agent-timeout-multiplier", type=float, default=None,
                    metavar="MULT",
                    help="Multiplier for the task's [agent] timeout_sec (e.g. 3.0 -> 1800s "
                         "when the task default is 600s). Pass for slow models like "
                         "deepseek-pro to avoid AgentTimeoutError.")
    args = ap.parse_args()

    run_tag = args.run_tag or (
        f"poise-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", run_tag):
        ap.error("--run-tag must be 1-80 filesystem-safe characters")

    workdir = PROJECT_ROOT / args.workdir
    jobs_root = PROJECT_ROOT / args.jobs_dir
    jobs_dir = jobs_root if args.harvest_only else jobs_root / run_tag
    limits = {"cpu": args.limit_cpu, "mem": args.limit_mem, "disk": args.limit_disk}
    output = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT / "eval" / "docker" / "throttled_results.csv"
    )

    if args.harvest_only:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from run_docker_eval import harvest_results
        if jobs_dir.exists():
            # Per-variant layout: jobs_dir/<timestamp>/<trial>/ — one timestamp
            # dir per variant. _find_trial_dirs already recurses both layouts,
            # so pass jobs_dir itself (not the last subdir).
            harvest_results(jobs_dir, Path(args.sample), output)
        return 0

    print(f"{'='*60}")
    print(f" Resource-Aware Throttled Runner")
    print(f" Sample:  {args.sample}")
    print(f" Workdir: {workdir}")
    print(f" Run tag: {run_tag}")
    print(f" Limits:  cpu={limits['cpu']}  mem={limits['mem']}G  disk={limits['disk']}G")
    print(f" Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # Check connectivity
    usage = get_resource_usage()
    if usage["ok"]:
        print(f"\nDaytona: {usage['active']} active sandboxes, "
              f"using cpu={usage['used_cpu']} mem={usage['used_mem']}G disk={usage['used_disk']}G")
    else:
        print(
            "\nERROR: Could not query Daytona capacity; refusing to enter "
            "an unbounded throttle loop",
            file=sys.stderr,
        )
        return 2

    print(
        "\nRemote sandbox deletion is disabled; Harbor timeout and explicit "
        "provider-side cleanup are used."
    )
    failed = run_all(
        workdir,
        jobs_dir,
        limits,
        args.agent,
        args.model,
        args.env,
        args.max_workers,
        agent_kwargs=args.agent_kwarg,
        limit_variants=args.limit_variants,
        agent_timeout_mult=args.agent_timeout_multiplier,
        run_tag=run_tag,
        agent_import_path=args.agent_import_path,
    )

    print(f"\nFinished: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
