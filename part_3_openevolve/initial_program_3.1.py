import subprocess
import time

# ============================================================
# Initial scheduling program for OpenEvolve
# LLM will only modify the EVOLVE-BLOCK section
# ============================================================

def run_cmd(cmd):
    """Run a shell command and return output."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout, result.stderr

def create_job(name, image, node_label, cores, threads, run_cmd_str):
    """Create a Kubernetes job with given parameters."""
    yaml = f"""
apiVersion: batch/v1
kind: Job
metadata:
  name: {name}
spec:
  template:
    spec:
      containers:
      - image: {image}
        name: {name}
        imagePullPolicy: Always
        command: ["/bin/sh"]
        args: ["-c", "taskset -c {cores} {run_cmd_str} -n {threads}"]
      restartPolicy: Never
      nodeSelector:
        cca-project-nodetype: "{node_label}"
"""
    proc = subprocess.run(
        ["kubectl", "create", "-f", "-"],
        input=yaml, capture_output=True, text=True
    )
    print(f"Created {name}: {proc.stdout.strip()} {proc.stderr.strip()}")

def wait_for_job(name, timeout=600):
    """Wait for a job to complete."""
    print(f"Waiting for {name}...")
    result = subprocess.run(
        ["kubectl", "wait", "--for=condition=complete",
         f"job/{name}", f"--timeout={timeout}s"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"{name} completed!")
        return True
    else:
        print(f"{name} failed or timed out: {result.stderr}")
        return False

def delete_all_jobs():
    """Clean up all jobs."""
    subprocess.run("kubectl delete jobs --all --ignore-not-found=true",
                   shell=True, capture_output=True)
    subprocess.run("kubectl delete pods --all --ignore-not-found=true",
                   shell=True, capture_output=True)
    time.sleep(5)

# ============================================================
# EVOLVE-BLOCK-START
# LLM modifies this block to optimize the scheduling strategy
# ============================================================

def schedule():
    """
    Scheduling policy for 7 PARSEC jobs on 2 nodes.
    
    Available nodes:
    - node-a: 8 cores, cores 0-1 used by memcached, cores 2-7 available (6 cores)
    - node-b: 4 cores, cores 0-3 available
    
    SLO: memcached p95 latency < 1ms at 30K QPS
    CPU/LLC-sensitive jobs on node-a WILL violate the SLO.
    
    Job runtimes with 4 threads (seconds):
    streamcluster=140, canneal=93, freqmine=85,
    barnes=29, blackscholes=25, vips=17, radix=13
    """
    
    # Phase 1: Start longest jobs first
    # node-b: streamcluster (most CPU sensitive, longest)
    create_job("parsec-streamcluster", "anakli/cca:parsec_streamcluster",
               "node-b", "0-3", 4,
               "./run -a run -S parsec -p streamcluster -i native")
    
    # node-a: blackscholes + radix (low interference, fast)
    create_job("parsec-blackscholes", "anakli/cca:parsec_blackscholes",
               "node-a", "2-3", 2,
               "./run -a run -S parsec -p blackscholes -i native")
    
    create_job("parsec-radix", "anakli/cca:splash2x_radix",
               "node-a", "4-7", 4,
               "./run -a run -S splash2x -p radix -i native")
    
    # Wait for fast jobs on node-a
    wait_for_job("parsec-blackscholes", timeout=120)
    wait_for_job("parsec-radix", timeout=120)
    
    # Phase 2: node-a cores 2-7 free, start vips + barnes
    create_job("parsec-vips", "anakli/cca:parsec_vips",
               "node-a", "2-5", 4,
               "./run -a run -S parsec -p vips -i native")
    
    create_job("parsec-barnes", "anakli/cca:splash2x_barnes",
               "node-a", "6-7", 2,
               "./run -a run -S splash2x -p barnes -i native")
    
    # Wait for streamcluster
    wait_for_job("parsec-streamcluster", timeout=600)
    
    # Phase 3: node-b free, start freqmine + canneal
    create_job("parsec-freqmine", "anakli/cca:parsec_freqmine",
               "node-b", "0-3", 4,
               "./run -a run -S parsec -p freqmine -i native")
    
    create_job("parsec-canneal", "anakli/cca:parsec_canneal",
               "node-b", "0-3", 4,
               "./run -a run -S parsec -p canneal -i native")
    
    # Wait for all remaining
    wait_for_job("parsec-vips", timeout=300)
    wait_for_job("parsec-barnes", timeout=300)
    wait_for_job("parsec-freqmine", timeout=600)
    wait_for_job("parsec-canneal", timeout=600)

# EVOLVE-BLOCK-END
# ============================================================

if __name__ == "__main__":
    print("=== Starting scheduling ===")
    start = time.time()
    
    # Label nodes
    subprocess.run("kubectl label nodes node-a-8core-tjn4 cca-project-nodetype=node-a --overwrite",
                   shell=True, capture_output=True)
    subprocess.run("kubectl label nodes node-b-4core-rj6b cca-project-nodetype=node-b --overwrite",
                   shell=True, capture_output=True)
    
    delete_all_jobs()
    schedule()
    
    makespan = time.time() - start
    print(f"=== Total makespan: {makespan:.1f}s ===")
    
    # Save results
    subprocess.run("kubectl get pods -o json > pods_openevolve.json", shell=True)
