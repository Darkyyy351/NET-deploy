#!/usr/bin/env bash

set -u

section() {
  printf '\n== %s ==\n' "$1"
}

read_file() {
  local path="$1"

  if [[ -r "$path" ]]; then
    tr -d '\0' < "$path"
    printf '\n'
  else
    printf 'unavailable (%s)\n' "$path"
  fi
}

section "Host"
printf 'Hostname: %s\n' "$(hostname)"
printf 'Kernel:   %s\n' "$(uname -srmo)"
printf 'Model:    '
read_file /proc/device-tree/model

section "Kernel power states"
printf 'state:     '
read_file /sys/power/state
printf 'mem_sleep: '
read_file /sys/power/mem_sleep

section "RTC wake"
if [[ -d /sys/class/rtc/rtc0 ]]; then
  printf 'rtc0: present\n'
  printf 'name: '
  read_file /sys/class/rtc/rtc0/name
  printf 'wakealarm: '
  read_file /sys/class/rtc/rtc0/wakealarm
else
  printf 'rtc0: unavailable\n'
fi

section "Raspberry Pi bootloader"
if command -v rpi-eeprom-config >/dev/null 2>&1; then
  rpi-eeprom-config 2>/dev/null | grep -E '^(POWER_OFF_ON_HALT|WAKE_ON_GPIO|WAIT_FOR_POWER_BUTTON)=' || \
    printf 'Power settings were not reported without elevated access.\n'
else
  printf 'rpi-eeprom-config: not installed\n'
fi

section "Power button input"
if command -v grep >/dev/null 2>&1 && [[ -r /proc/bus/input/devices ]]; then
  grep -i -A 4 -B 2 'pwr\|power' /proc/bus/input/devices || printf 'No power-button input entry found.\n'
else
  printf 'Input device list unavailable.\n'
fi

section "USB runtime power policy"
usb_controls=(/sys/bus/usb/devices/*/power/control)

if [[ -e "${usb_controls[0]}" ]]; then
  for control in "${usb_controls[@]}"; do
    printf '%s: %s\n' "${control%/power/control}" "$(<"$control")"
  done
else
  printf 'No USB power controls found.\n'
fi

section "NET containers"
if command -v docker >/dev/null 2>&1; then
  docker ps --filter 'name=net-' --format '{{.Names}}: {{.Status}}' || true
else
  printf 'docker: not installed\n'
fi

section "Result"
printf '%s\n' 'Read-only inspection complete. No power setting was changed and the CM5 was not suspended.'
