#!/bin/bash
# fetch_and_rminer.sh — Fetch blobs and run RMiner for all repos
# Usage: bash scripts/fetch_and_rminer.sh

set -e
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd /home/sing/refactor_project

RMINER="tools/RefactoringMiner-3.0.10/bin/RefactoringMiner"

# All repos with their needed commit window sizes
# Format: name window_size
declare -A WINDOWS
WINDOWS[activemq]=500
WINDOWS[accumulo]=500
WINDOWS[james-project]=500
WINDOWS[openmeetings]=300
WINDOWS[jmeter]=500
WINDOWS[skywalking]=500
WINDOWS[jclouds]=500
WINDOWS[qpid-jms-amqp-0-x]=500
WINDOWS[attic-polygene-java]=500
WINDOWS[incubator-brooklyn]=500
WINDOWS[incubator-druid]=500
WINDOWS[systemml]=500
WINDOWS[oozie]=300
WINDOWS[apex-malhar]=500
WINDOWS[ode]=300
WINDOWS[incubator-gobblin]=500
WINDOWS[servicecomb-pack]=300
WINDOWS[falcon]=300
WINDOWS[myfaces-extcdi]=300
WINDOWS[incubator-pinot]=500
WINDOWS[brooklyn-library]=300
WINDOWS[deltaspike]=300
WINDOWS[incubator-iotdb]=500
WINDOWS[apex-core]=500
WINDOWS[myfaces-trinidad]=300
WINDOWS[incubator-shardingsphere]=1000
WINDOWS[incubator-dolphinscheduler]=500
WINDOWS[incubator-taverna-language]=300
WINDOWS[hadoop-ozone]=500

for REPO in "${!WINDOWS[@]}"; do
    REFS_JSON="data/${REPO}_refs.json"
    REPO_DIR="data/${REPO}"
    WINDOW=${WINDOWS[$REPO]}

    # Skip if already done
    if [ -f "$REFS_JSON" ] && [ -s "$REFS_JSON" ]; then
        SIZE=$(python3 -c "import json; d=json.load(open('$REFS_JSON')); c=[cc for cc in d.get('commits',[]) if cc.get('refactorings')]; print(len(c))")
        if [ "$SIZE" -gt 0 ]; then
            echo "[$REPO] Already has $SIZE refactoring commits, skip"
            continue
        fi
    fi

    if [ ! -d "$REPO_DIR/.git" ]; then
        echo "[$REPO] Not cloned, skip"
        continue
    fi

    echo "[$REPO] Fetching blobs..."
    # Convert blobless to full clone by fetching all blobs
    git -C "$REPO_DIR" fetch --refetch --filter=blob:none origin 2>/dev/null || \
    git -C "$REPO_DIR" config remote.origin.promisor false 2>/dev/null || true

    # Alternative: just fetch the range we need
    git -C "$REPO_DIR" checkout HEAD -- . 2>/dev/null || true

    # Get commit range
    END_SHA=$(git -C "$REPO_DIR" rev-parse HEAD)
    START_SHA=$(git -C "$REPO_DIR" log --format=%H --skip=$WINDOW -1 2>/dev/null)
    if [ -z "$START_SHA" ]; then
        START_SHA=$(git -C "$REPO_DIR" rev-list --max-parents=0 HEAD | head -1)
    fi

    echo "[$REPO] Running RMiner -bc ($WINDOW commits): ${START_SHA:0:7}..${END_SHA:0:7}"
    timeout 3600 $RMINER -bc "$REPO_DIR" "$START_SHA" "$END_SHA" -json "$REFS_JSON" 2>&1 | tail -3

    if [ -f "$REFS_JSON" ]; then
        TOTAL=$(python3 -c "import json; d=json.load(open('$REFS_JSON')); c=[cc for cc in d.get('commits',[]) if cc.get('refactorings')]; print(sum(len(cc.get('refactorings',[])) for cc in c))")
        echo "[$REPO] Found $TOTAL refactorings"
    else
        echo "[$REPO] RMiner FAILED"
    fi

    # Update progress
    python3 -c "
import json
with open('results/all_repos_progress.json') as f:
    p = json.load(f)
p.setdefault('$REPO', {})['rminer'] = True
with open('results/all_repos_progress.json', 'w') as f:
    json.dump(p, f, indent=2)
"

    echo ""
done

echo "=== RMiner phase complete ==="
