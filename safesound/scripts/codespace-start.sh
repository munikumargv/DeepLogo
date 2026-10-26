#!/usr/bin/env bash
# Start the SafeSound server in the background; on first start, fill it with demo data.
set -e
cd "$(dirname "$0")/.."
fresh=0
[ -f safesound.db ] || fresh=1
nohup python -m safesound.server --host 0.0.0.0 --port 8000 > /tmp/safesound-server.log 2>&1 &
for _ in $(seq 1 30); do
  curl -sf http://localhost:8000/api/health > /dev/null && break
  sleep 1
done
if [ "$fresh" = 1 ]; then
  python -m safesound.simulator --server http://localhost:8000 --background 60
fi
echo "SafeSound is running on port 8000 (log: /tmp/safesound-server.log)"
