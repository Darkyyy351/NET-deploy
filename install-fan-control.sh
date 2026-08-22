#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if (( EUID != 0 )); then
  printf 'Run this installer with sudo: sudo ./install-fan-control.sh\n' >&2
  exit 1
fi

for path in \
  /sys/class/thermal/thermal_zone0/policy \
  /sys/class/thermal/thermal_zone0/temp \
  /sys/class/thermal/cooling_device0/cur_state \
  /sys/class/thermal/cooling_device0/max_state \
  /sys/class/thermal/cooling_device0/type; do
  [[ -r "$path" ]] || { printf 'Required CM5 thermal path is missing: %s\n' "$path" >&2; exit 1; }
done

[[ "$(cat /sys/class/thermal/cooling_device0/type)" == "pwm-fan" ]] || { printf 'cooling_device0 is not pwm-fan.\n' >&2; exit 1; }
[[ "$(cat /sys/class/thermal/cooling_device0/max_state)" == "4" ]] || { printf 'Expected max fan state 4.\n' >&2; exit 1; }
grep -qw user_space /sys/class/thermal/thermal_zone0/available_policies || { printf 'user_space thermal policy is unavailable.\n' >&2; exit 1; }

install -D -m 0755 "$SCRIPT_DIR/fan-control/net-fan-control.py" /usr/local/lib/net/net-fan-control.py
install -D -m 0644 "$SCRIPT_DIR/fan-control/net-fan-control.service" /etc/systemd/system/net-fan-control.service
systemctl daemon-reload
systemctl enable net-fan-control.service
systemctl restart net-fan-control.service

for _ in {1..20}; do
  [[ -S /run/net-fan-control/control.sock ]] && break
  sleep 0.25
done

[[ -S /run/net-fan-control/control.sock ]] || { systemctl status --no-pager net-fan-control.service; exit 1; }
printf 'NET fan control helper installed. Kernel step_wise remains the default mode.\n'
