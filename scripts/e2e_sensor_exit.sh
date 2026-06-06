#!/usr/bin/env bash
# e2e_sensor_exit.sh — End-to-end test for the sensor-driven exit flow.
#
# Flow verified:
#   1. POST /gate/entrance  -> creates an active session, assigns a spot
#   2. (you) wave a hand at that spot's sensor; serial_bridge.py POSTs
#      /sensors/ping (occupied=1) then, on hand removal, /sensors/ping 0
#      AND /sensors/exit -> session closed + paid PaymentTransaction
#   3. GET /transactions/<plate> -> shows the closed session w/ paid txn
#
# Prereqs: Tailscale up; backend running; serial_bridge.py running against the
# Arduino flashed with arduino/parking_sensors. SpotDevices + PricingPolicies
# rows must exist (see README.md "DB prerequisites for full operation").
#
# Usage: BASE_URL=http://<tailscale-ip>:8000 API_KEY=<key> ./scripts/e2e_sensor_exit.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-CHANGE_ME}"
LOT_ID="${LOT_ID:-1}"
PLATE="${PLATE:-12가3456}"
H=(-H "X-API-Key: ${API_KEY}" -H "Content-Type: application/json")

echo "== 1. Entrance: create active session for ${PLATE} =="
curl -sS -X POST "${BASE_URL}/gate/entrance" "${H[@]}" \
  -d "{\"lot_id\": ${LOT_ID}, \"plate_number\": \"${PLATE}\"}"
echo

echo "== 2. Occupancy snapshot (expect the assigned spot occupied) =="
curl -sS "${BASE_URL}/spots"
echo

echo ">> Now wave a hand at the assigned spot's sensor, then remove it."
echo ">> The running serial_bridge.py should POST /sensors/ping and /sensors/exit."
read -r -p "Press ENTER once you've waved and the bridge logged an [EXIT]... "

echo "== 3. Transactions for ${PLATE} (expect a 'paid' txn, exit_time set) =="
curl -sS "${BASE_URL}/transactions/${PLATE}" "${H[@]}"
echo

echo "== 4. Dashboard metrics =="
curl -sS "${BASE_URL}/dashboard/metrics" "${H[@]}"
echo
echo "PASS criteria: transactions list shows payment_status=paid with a non-null exit_time."
