#!/usr/bin/env bash
# install_services.sh — install + enable the Hexa-Vision systemd units.
#
# Requires root (writes to /etc/systemd/system). Run from anywhere:
#     sudo ./deploy/install_services.sh
#
# It links the unit files from this repo into /etc/systemd/system (so the repo
# stays the single source of truth), reloads systemd, and ENABLES all units for
# boot. It does NOT start them — start manually (see README "Production
# Deployment"). Re-running is safe (idempotent).
#
# Three services (hybrid hardware split):
#   hexavision-backend  — uvicorn (.venv)
#   hexavision-reader   — ai_vision/unified_serial_reader.py (.venv_capture): serial
#                         occupancy only (READER_ENABLE_CAMERA=0), sole owner
#                         of /dev/ttyUSB0.
#   hexavision-capture  — ai_vision/pi_client.py (.venv_capture): sole owner of
#                         the CSI camera; pushes JPEG frames to the Vision PC.
#
# The serial bus still has a single owner: the old hexavision-bridge unit (a
# second serial process) is obsolete and any previously-installed copy is
# removed. The camera is a different device, so hexavision-capture is a first-
# class unit here (not the old serial-splitting unit that once shared the name).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "This script must run as root: sudo ./deploy/install_services.sh" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$REPO_DIR/deploy/systemd"
UNITS=(hexavision-backend.service hexavision-reader.service hexavision-capture.service)
OBSOLETE=(hexavision-bridge.service)

# Remove the superseded serial-splitting unit if a previous install left it behind.
for u in "${OBSOLETE[@]}"; do
  if [[ -e "/etc/systemd/system/$u" ]]; then
    echo "Removing obsolete $u"
    systemctl disable --now "$u" 2>/dev/null || true
    rm -f "/etc/systemd/system/$u"
  fi
done

for u in "${UNITS[@]}"; do
  echo "Linking $u -> /etc/systemd/system/$u"
  ln -sf "$UNIT_SRC/$u" "/etc/systemd/system/$u"
done

echo "Reloading systemd daemon..."
systemctl daemon-reload

for u in "${UNITS[@]}"; do
  echo "Enabling $u (boot start; NOT started now)"
  systemctl enable "$u"
done

echo
echo "Done. All units are enabled for boot but NOT started. Start them with:"
echo "    sudo systemctl start hexavision-backend"
echo "    sudo systemctl start hexavision-reader"
echo "    sudo systemctl start hexavision-capture"
