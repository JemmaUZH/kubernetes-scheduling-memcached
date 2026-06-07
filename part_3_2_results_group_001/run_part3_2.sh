#!/bin/bash

mkdir -p part_3_2_results_group_001

for i in 1 2 3; do
    echo "=== Run $i ==="

    ssh -i ~/.ssh/cloud-computing -o StrictHostKeyChecking=no \
        ubuntu@104.155.1.253 "> /tmp/mcperf_live.txt"

    kubectl delete jobs --all --ignore-not-found=true
    kubectl delete pods -l job-name --ignore-not-found=true
    sleep 5

    python3 openevolve_out/best/best_program.py

    kubectl get pods -o json > part_3_2_results_group_001/pods_${i}.json

    scp -i ~/.ssh/cloud-computing -o StrictHostKeyChecking=no \
        ubuntu@104.155.1.253:/tmp/mcperf_live.txt \
        part_3_2_results_group_001/mcperf_${i}.txt

    echo "mcperf_${i} lines: $(wc -l < part_3_2_results_group_001/mcperf_${i}.txt)"
    echo "Run $i saved!"
    sleep 10
done