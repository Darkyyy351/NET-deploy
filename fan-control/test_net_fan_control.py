import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path


class FanControllerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.paths = {
            "NET_FAN_SOCKET": root / "control.sock",
            "NET_FAN_POLICY_PATH": root / "policy",
            "NET_FAN_TEMP_PATH": root / "temp",
            "NET_FAN_STATE_PATH": root / "state",
            "NET_FAN_MAX_STATE_PATH": root / "max_state",
            "NET_FAN_TYPE_PATH": root / "type",
        }

        for name, path in self.paths.items():
            os.environ[name] = str(path)

        self.paths["NET_FAN_POLICY_PATH"].write_text("step_wise\n", encoding="ascii")
        self.paths["NET_FAN_TEMP_PATH"].write_text("56750\n", encoding="ascii")
        self.paths["NET_FAN_STATE_PATH"].write_text("1\n", encoding="ascii")
        self.paths["NET_FAN_MAX_STATE_PATH"].write_text("4\n", encoding="ascii")
        self.paths["NET_FAN_TYPE_PATH"].write_text("pwm-fan\n", encoding="ascii")

        script = Path(__file__).with_name("net-fan-control.py")
        spec = importlib.util.spec_from_file_location("net_fan_control", script)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.controller = self.module.FanController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manual_state_times_out_to_kernel_control(self):
        status = self.controller.start_test(2, 60)
        self.assertEqual(status["mode"], "manual_test")
        self.assertEqual(self.paths["NET_FAN_POLICY_PATH"].read_text().strip(), "user_space")
        self.assertEqual(self.paths["NET_FAN_STATE_PATH"].read_text().strip(), "2")

        self.controller.active_until = time.monotonic() - 1
        self.controller.tick()

        self.assertEqual(self.paths["NET_FAN_POLICY_PATH"].read_text().strip(), "step_wise")
        self.assertEqual(self.paths["NET_FAN_STATE_PATH"].read_text().strip(), "4")
        self.assertEqual(self.controller.status()["reason"], "timeout")

    def test_off_and_temperature_guards(self):
        self.paths["NET_FAN_TEMP_PATH"].write_text("60000\n", encoding="ascii")
        with self.assertRaises(self.module.FanControlError):
            self.controller.start_test(0, 60)

        self.paths["NET_FAN_TEMP_PATH"].write_text("75000\n", encoding="ascii")
        with self.assertRaises(self.module.FanControlError):
            self.controller.start_test(4, 60)


if __name__ == "__main__":
    unittest.main()
