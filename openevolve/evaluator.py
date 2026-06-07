import subprocess
import time
import json
import os
from openevolve.evaluation_result import EvaluationResult

# UPDATE THESE after cluster rebuild!
CLIENT_MEASURE_IP = "35.190.218.229"
SSH_KEY = os.path.expanduser("~/.ssh/cloud-computing")
MCPERF_LOG = "/tmp/mcperf_live.txt"
NODE_A_NAME = "node-a-8core-tjn4"
NODE_B_NAME = "node-b-4core-rj6b"

def setup_nodes():
    subprocess.run(f"kubectl label nodes {NODE_A_NAME} cca-project-nodetype=node-a --overwrite",
                   shell=True, capture_output=True)
    subprocess.run(f"kubectl label nodes {NODE_B_NAME} cca-project-nodetype=node-b --overwrite",
                   shell=True, capture_output=True)

def clean_cluster():
    subprocess.run("kubectl delete jobs --all --ignore-not-found=true",
                   shell=True, capture_output=True)
    subprocess.run("kubectl delete pods --all --ignore-not-found=true",
                   shell=True, capture_output=True)
    time.sleep(8)

def get_max_p95():
    """Get max p95 from mcperf log via SSH. Returns ms."""
    try:
        cmd = (f"ssh -i {SSH_KEY} -o StrictHostKeyChecking=no "
               f"-o ConnectTimeout=10 ubuntu@{CLIENT_MEASURE_IP} "
               f"\"cat {MCPERF_LOG} 2>/dev/null | grep '^read' | awk '{{print $13}}' | sort -n | tail -1\"")
        result = subprocess.run(cmd, shell=True, capture_output=True,
                                text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip()) / 1000.0
    except Exception as e:
        print(f"Warning: could not get p95: {e}")
    return 0.5  # assume ok if can't measure

def clear_mcperf_log():
    try:
        cmd = (f"ssh -i {SSH_KEY} -o StrictHostKeyChecking=no "
               f"-o ConnectTimeout=10 ubuntu@{CLIENT_MEASURE_IP} "
               f"'> {MCPERF_LOG}'")
        subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
    except Exception:
        pass

def get_makespan():
    """Get makespan in seconds from pod timestamps."""
    result = subprocess.run("kubectl get pods -o json",
                            shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        from datetime import datetime
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        starts, ends = [], []
        for item in data.get("items", []):
            if "parsec" not in item.get("metadata", {}).get("name", ""):
                continue
            status = item.get("status", {})
            start = status.get("startTime")
            for cond in status.get("conditions", []):
                if cond.get("type") == "Complete" and cond.get("status") == "True":
                    end = cond.get("lastTransitionTime")
                    if start and end:
                        starts.append(datetime.strptime(start, fmt))
                        ends.append(datetime.strptime(end, fmt))
        if starts and ends:
            return (max(ends) - min(starts)).total_seconds()
    except Exception as e:
        print(f"Error parsing makespan: {e}")
    return None

def evaluate(program_path: str) -> EvaluationResult:
    print(f"\n{'='*50}\nEvaluating: {program_path}\n{'='*50}")

    setup_nodes()
    clean_cluster()
    clear_mcperf_log()

    start = time.time()
    try:
        result = subprocess.run(
            ["python3", program_path],
            timeout=900
        )
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        success = False
    wall_time = time.time() - start

    if not success:
        print("Program failed!")
        return EvaluationResult(
            metrics={"makespan": 9999.0, "p95_ms": 9999.0,
                     "slo_ok": 0.0, "combined_score": -9999.0},
        )

    makespan = get_makespan() or wall_time
    p95 = get_max_p95()
    slo_ok = p95 < 1.0

    print(f"Makespan: {makespan:.1f}s, p95: {p95:.3f}ms, SLO ok: {slo_ok}")

    if slo_ok:
        combined_score = -makespan
    else:
        combined_score = -makespan - 10000.0 * (p95 - 1.0)

    print(f"Combined score: {combined_score:.1f}")

    subprocess.run("kubectl get pods -o json > pods_openevolve_latest.json",
                   shell=True, capture_output=True)

    return EvaluationResult(
        metrics={
            "makespan": makespan,
            "p95_ms": p95,
            "slo_ok": 1.0 if slo_ok else 0.0,
            "combined_score": combined_score
        }
    )
