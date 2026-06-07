# Kubernetes Scheduling for Latency-Sensitive & Batch Workloads

ETH Zürich | Cloud Computing Architecture | Spring 2026 | Group 001

## Overview

This project explores scheduling strategies for co-running latency-sensitive and batch applications in a cloud cluster using **Kubernetes** and **Docker** on Google Cloud.

The project has four parts:

- **Part 1** — Measure tail latency of `memcached` under various hardware resource interference (CPU, L1/L2/LLC cache, memory bandwidth) using iBench microbenchmarks.
- **Part 2** — Profile 7 PARSEC/SPLASH-2x batch workloads for interference sensitivity and parallel scalability (1/2/4/8 threads).
- **Part 3** — Co-schedule memcached + all 7 batch jobs on a heterogeneous Kubernetes cluster. Design a hand-crafted policy and an LLM-evolved policy (via OpenEvolve) to minimize makespan while keeping memcached 95th-percentile latency < 1ms at 30K QPS.
- **Part 4** — Dynamic scheduling on a single 4-core VM with variable memcached load. Implement a Python controller that scales memcached between 1 and 3 cores based on CPU utilization, pausing/resuming batch jobs accordingly.

---

## Repository Structure

```
├── part_3_1_results_group_001/     # Part 3 Task 1: hand-crafted scheduling policy results
│   ├── pods_1/2/3.json             # kubectl get pods -o json output
│   ├── mcperf_1/2/3.txt            # mcperf latency measurements
│   └── yaml/                       # Modified PARSEC job YAML files
│
├── part_3_2_results_group_001/     # Part 3 Task 2: OpenEvolve-generated policy results
│   ├── pods_1/2/3.json
│   ├── mcperf_1/2/3.txt
│   ├── run_part3_2.sh
│   └── openevolve_*.zip
│
├── part_3_openevolve/              # OpenEvolve artifacts
│   ├── initial_program_3.1.py      # Starting scheduling policy
│   ├── evaluator.py                # Score function for evolution
│   ├── openevolve_*.log            # Evolution run log
│   └── checkpoint_20/             # Best evolved program checkpoint
│       └── best_program.py
│
├── part_4_scripts/                 # Part 4 controller & setup
│   ├── scheduler.py                # Main dynamic scheduler (Python + Docker SDK)
│   ├── run_experiment.sh           # Experiment automation script
│   └── part4.yaml                  # Kubernetes cluster config
│
├── part_4_3_results_group_001/     # Part 4 Task 3 results
│   ├── jobs_1/2/3.txt              # Container execution logs
│   └── mcperf_1/2/3.txt
│
└── part_4_4_results_group_001/     # Part 4 Task 4 results
    ├── jobs_1/2/3.txt
    └── mcperf_1/2/3.txt
```

---

## Part 4 Scheduler

The scheduler (`part_4_scripts/scheduler.py`) runs directly on the `memcache-server` VM and manages 7 PARSEC/SPLASH-2x batch jobs via the Docker Python SDK.

**Key design decisions:**
- Memcached is pinned to cores 0–N via `taskset`; batch jobs use the remaining cores.
- Memcached scales between **1 core** and **3 cores** based on CPU utilization thresholds (up at >75%, down at <60%).
- Batch jobs are slotted across cores 1, 2, 3. When memcached expands to 3 cores, slots 0 and 1 are **paused**; when it shrinks back, they are **resumed**.
- The last remaining job is expanded to all available batch cores to minimize makespan.

**Batch jobs and launch order:**

| Job | Image | Threads | Expected Runtime (s) |
|---|---|---|---|
| freqmine | parsec_freqmine | 1 | 180 |
| streamcluster | parsec_streamcluster | 1 | 143 |
| barnes | splash2x_barnes | 1 | 98 |
| canneal | parsec_canneal | 1 | 139 |
| vips | parsec_vips | 1 | 48 |
| radix | splash2x_radix | 1 | 22 |
| blackscholes | parsec_blackscholes | 2 | 92 |

---

## Requirements

- Google Cloud account (ETH email)
- `kubectl`, `kops`, `gcloud` CLI tools
- Python 3.10+ with `docker` and `psutil` packages
- Docker installed on the memcache-server VM

---

## SLO Targets

| Part | Metric | Target |
|---|---|---|
| Part 3 | p95 latency @ 30K QPS | < 1 ms |
| Part 4 | p95 latency (dynamic load) | < 0.8 ms |
