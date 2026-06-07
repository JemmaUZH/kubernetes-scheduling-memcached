#!/bin/bash

MEMCACHE_VM="memcache-server-3lv0"
AGENT_VM="client-agent-dc77"
MEASURE_VM="client-measure-k85b"
ZONE="europe-west4-a"
PROJECT="cca-eth-2026-group-1-491820"
MEMCACHED_IP="10.0.16.4"
AGENT_IP="10.0.16.5"

RUN_ID=$(date +%Y%m%d_%H%M%S)
RESULTS="results/run_${RUN_ID}"
mkdir -p "$RESULTS"
echo "=== Run $RUN_ID — results in $RESULTS ==="

# 0. Cleanup leftovers from previous run
echo "--- Cleaning up previous run ---"
gcloud compute ssh $MEASURE_VM --zone=$ZONE --project=$PROJECT --command="
  pkill mcperf 2>/dev/null; echo 'mcperf killed'
" &
gcloud compute ssh $AGENT_VM --zone=$ZONE --project=$PROJECT --command="
  pkill mcperf 2>/dev/null; echo 'agent killed'
" &
gcloud compute ssh $MEMCACHE_VM --zone=$ZONE --project=$PROJECT --command="
  docker ps -q | xargs -r docker kill 2>/dev/null
  docker ps -aq | xargs -r docker rm 2>/dev/null
  echo 'containers cleaned'
" &
wait
sleep 2

# 1. Load memcached DB
echo "--- Loading memcached DB ---"
gcloud compute ssh $MEASURE_VM --zone=$ZONE --project=$PROJECT --command="
  cd memcache-perf-dynamic && ./mcperf -s $MEMCACHED_IP --loadonly
"

# 2. Start mcperf agent
echo "--- Starting mcperf agent ---"
gcloud compute ssh $AGENT_VM --zone=$ZONE --project=$PROJECT --command="
  pkill mcperf 2>/dev/null; sleep 1
  sudo apt-get install -y screen -qq
  screen -dmS mcperf-agent bash -c 'cd ~/memcache-perf-dynamic && ./mcperf -T 8 -A > /tmp/agent.log 2>&1'
  sleep 2 && pgrep mcperf && echo 'agent ready' || echo 'agent FAILED'
"
sleep 2

# 3. Start mcperf load in background, output saved on client-measure
echo "--- Starting mcperf load (1800s) ---"
gcloud compute ssh $MEASURE_VM --zone=$ZONE --project=$PROJECT --command="
  nohup bash -c 'cd memcache-perf-dynamic && stdbuf -oL ./mcperf -s $MEMCACHED_IP -a $AGENT_IP \
    --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 1650 \
    --qps_interval 5 --qps_min 5000 --qps_max 110000 \
    --qps_seed 2345 \
    > /tmp/mcperf_$RUN_ID.log 2>&1' & disown
  echo 'mcperf load started'
" &
sleep 5

# Sanity check: verify agent and mcperf load are alive before committing to a 20+ min run
echo "--- Sanity check ---"
gcloud compute ssh $AGENT_VM --zone=$ZONE --project=$PROJECT --command="pgrep mcperf && echo 'agent OK' || echo 'agent DEAD — aborting'" | grep -q "DEAD" && { echo "Agent not running, aborting."; exit 1; }
gcloud compute ssh $MEASURE_VM --zone=$ZONE --project=$PROJECT --command="pgrep mcperf && echo 'measure OK' || echo 'measure DEAD — aborting'" | grep -q "DEAD" && { echo "mcperf load not running, aborting."; exit 1; }
echo "--- Both mcperf processes alive, proceeding ---"

# 4. Run scheduler in foreground — blocks until all batch jobs done
echo "--- Starting scheduler ---"
gcloud compute ssh $MEMCACHE_VM --zone=$ZONE --project=$PROJECT --command="
  source ~/venv/bin/activate && cd ~ && python3 scheduler.py > ~/scheduler_stdout.log 2>&1
"
echo "--- Scheduler done — waiting for mcperf to finish (max 1600s) ---"

# 5. Wait for mcperf to finish naturally
WAIT_START=$(date +%s)
while true; do
  RUNNING=$(gcloud compute ssh $MEASURE_VM --zone=$ZONE --project=$PROJECT --command="pgrep mcperf > /dev/null && echo 1 || echo 0" 2>/dev/null | tr -d '[:space:]')
  [ "$RUNNING" = "0" ] && { echo "mcperf finished"; break; }
  NOW=$(date +%s)
  ELAPSED=$((NOW - WAIT_START))
  [ $ELAPSED -gt 1600 ] && { echo "mcperf timeout — collecting anyway"; break; }
  echo "  mcperf still running (${ELAPSED}s elapsed)..."
  sleep 30
done
gcloud compute ssh $AGENT_VM --zone=$ZONE --project=$PROJECT --command="pkill mcperf 2>/dev/null; true" &

# 6. Collect everything
echo "--- Collecting results ---"
gcloud compute scp "$MEMCACHE_VM:~/scheduler_stdout.log" "$RESULTS/" --zone=$ZONE --project=$PROJECT
gcloud compute scp "$MEMCACHE_VM:~/log*.txt"             "$RESULTS/" --zone=$ZONE --project=$PROJECT
gcloud compute scp "$MEASURE_VM:/tmp/mcperf_$RUN_ID.log" "$RESULTS/mcperf.log" --zone=$ZONE --project=$PROJECT

echo "=== Done. Results:"
ls -lh "$RESULTS/"
