"""Run the same high-rate input workload over adb or the production TLS relay."""
import os
from pathlib import Path
import runpy

from inputsharex.server import ScrcpyServer

original_init = ScrcpyServer.__init__
original_start = ScrcpyServer.start
original_stop = ScrcpyServer.stop


def init(self, *args, **kwargs):
    kwargs["direct"] = os.environ.get("INPUTSHAREX_TRANSPORT", "adb") == "tls"
    original_init(self, *args, **kwargs)


def start(self):
    sock = original_start(self)
    print("transport:", self.transport, "fallback:", self.direct_error, flush=True)
    if os.environ.get("INPUTSHAREX_POINTER_PROBE") == "1" and not getattr(self, "_test_pointer", None):
        from inputsharex.pointer import PointerMonitor
        self._test_pointer = PointerMonitor(self.adb, self.serial,
            str(Path(self.jar_path).with_name("inputsharex-relay.jar")), lambda _: None)
        self._test_pointer.start()
        self._test_pointer.set_enabled(True, 1234, 0)
    return sock


def stop(self):
    probe = getattr(self, "_test_pointer", None)
    if probe:
        probe.stop()
        self._test_pointer = None
    return original_stop(self)


ScrcpyServer.__init__ = init
ScrcpyServer.start = start
ScrcpyServer.stop = stop
scenario = "transition_jitter.py" if os.environ.get("INPUTSHAREX_SCENARIO") == "transition" else "fast_motion_jitter.py"
runpy.run_path(str(Path(__file__).with_name(scenario)), run_name="__main__")
