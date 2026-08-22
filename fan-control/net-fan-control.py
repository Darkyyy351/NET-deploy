#!/usr/bin/env python3

import json
import math
import os
import signal
import socket
import sys
import time
from pathlib import Path


SOCKET_PATH = Path(os.environ.get("NET_FAN_SOCKET", "/run/net-fan-control/control.sock"))
POLICY_PATH = Path(os.environ.get("NET_FAN_POLICY_PATH", "/sys/class/thermal/thermal_zone0/policy"))
TEMP_PATH = Path(os.environ.get("NET_FAN_TEMP_PATH", "/sys/class/thermal/thermal_zone0/temp"))
STATE_PATH = Path(os.environ.get("NET_FAN_STATE_PATH", "/sys/class/thermal/cooling_device0/cur_state"))
MAX_STATE_PATH = Path(os.environ.get("NET_FAN_MAX_STATE_PATH", "/sys/class/thermal/cooling_device0/max_state"))
TYPE_PATH = Path(os.environ.get("NET_FAN_TYPE_PATH", "/sys/class/thermal/cooling_device0/type"))

MAX_TEST_SECONDS = 60
OFF_LIMIT_MC = 60000
TEMPERATURE_GUARD_MC = 75000


class FanControlError(Exception):
    pass


class FanController:
    def __init__(self):
        self.active_until = 0.0
        self.requested_state = None
        self.last_reason = "system_control"
        self.original_policy = "step_wise"
        self.startup_error = None

        try:
            self._validate_hardware()
            if self._read(POLICY_PATH) == "user_space":
                self._restore("startup_recovery")
        except Exception as error:
            self.startup_error = str(error)

    @staticmethod
    def _read(path):
        return path.read_text(encoding="ascii").strip()

    @staticmethod
    def _write(path, value):
        path.write_text(f"{value}\n", encoding="ascii")

    def _validate_hardware(self):
        fan_type = self._read(TYPE_PATH)
        max_state = int(self._read(MAX_STATE_PATH))

        if fan_type != "pwm-fan":
            raise FanControlError(f"Unsupported cooling device: {fan_type}")
        if max_state != 4:
            raise FanControlError(f"Expected max fan state 4, received {max_state}")

    def _temperature_mc(self):
        return int(self._read(TEMP_PATH))

    def _is_active(self):
        return self.active_until > time.monotonic()

    def _restore(self, reason):
        try:
            self._write(STATE_PATH, 4)
        finally:
            self._write(POLICY_PATH, self.original_policy if self.original_policy != "user_space" else "step_wise")
            self.active_until = 0.0
            self.requested_state = None
            self.last_reason = reason

    def tick(self):
        if self.requested_state is None:
            return

        try:
            temperature = self._temperature_mc()
        except Exception:
            self._restore("sensor_error")
            return

        if temperature >= TEMPERATURE_GUARD_MC:
            self._restore("temperature_guard")
        elif time.monotonic() >= self.active_until:
            self._restore("timeout")

    def start_test(self, state, duration):
        if self.startup_error:
            raise FanControlError(self.startup_error)
        if not isinstance(state, int) or isinstance(state, bool) or state < 0 or state > 4:
            raise FanControlError("State must be an integer from 0 to 4")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 1 or duration > MAX_TEST_SECONDS:
            raise FanControlError("Duration must be from 1 to 60 seconds")

        temperature = self._temperature_mc()
        if temperature >= TEMPERATURE_GUARD_MC:
            raise FanControlError("Temperature guard is active at 75 C")
        if state == 0 and temperature >= OFF_LIMIT_MC:
            raise FanControlError("Fan Off is allowed only below 60 C")

        policy = self._read(POLICY_PATH)
        if policy != "user_space":
            self.original_policy = policy
            self._write(POLICY_PATH, "user_space")

        self._write(STATE_PATH, state)
        self.requested_state = state
        self.active_until = time.monotonic() + duration
        self.last_reason = "manual_test"
        return self.status()

    def stop(self):
        if self._read(POLICY_PATH) == "user_space" or self._is_active():
            self._restore("manual_stop")
        return self.status()

    def status(self):
        self.tick()

        if self.startup_error:
            return {
                "available": False,
                "mode": "unavailable",
                "error": self.startup_error,
                "maxTestSeconds": MAX_TEST_SECONDS
            }

        temperature = self._temperature_mc()
        active = self._is_active()
        return {
            "available": True,
            "mode": "manual_test" if active else "system",
            "policy": self._read(POLICY_PATH),
            "state": int(self._read(STATE_PATH)),
            "requestedState": self.requested_state,
            "maxState": int(self._read(MAX_STATE_PATH)),
            "temperatureC": round(temperature / 1000, 1),
            "remainingSeconds": max(0, math.ceil(self.active_until - time.monotonic())) if active else 0,
            "reason": self.last_reason,
            "maxTestSeconds": MAX_TEST_SECONDS,
            "offAllowed": temperature < OFF_LIMIT_MC
        }


def response(controller, request):
    action = request.get("action")
    if action == "status":
        return {"ok": True, "data": controller.status()}
    if action == "start":
        return {"ok": True, "data": controller.start_test(request.get("state"), request.get("duration", 60))}
    if action == "stop":
        return {"ok": True, "data": controller.stop()}
    raise FanControlError("Unsupported action")


def serve():
    controller = FanController()
    running = True

    def stop_service(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop_service)
    signal.signal(signal.SIGINT, stop_service)

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o660)
    server.listen(8)
    server.settimeout(0.5)

    try:
        while running:
            controller.tick()
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue

            with connection:
                try:
                    payload = connection.recv(8192)
                    request = json.loads(payload.decode("utf-8").splitlines()[0])
                    result = response(controller, request)
                except Exception as error:
                    result = {"ok": False, "error": str(error)}
                connection.sendall((json.dumps(result, separators=(",", ":")) + "\n").encode("utf-8"))
    finally:
        try:
            controller.stop()
        except Exception as error:
            print(f"NET fan control restore failed: {error}", file=sys.stderr)
        server.close()
        SOCKET_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    serve()
