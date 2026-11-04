"""Headless SeedSigner emulator.

Runs the real Controller, Views, Screens, Renderer, Camera and DecodeQR. Only
the hardware edge is replaced:

* display: records every frame drawn (FakeDisplay)
* camera:  plays a list of images as the live video stream, cycling at a set
           fps like an animated QR on a laptop screen (FakeVideoStream)
* buttons: a script of key presses; the screen is captured just before each
           press (ScriptedButtons)

Import this module before anything from `seedsigner`.

Command line, e.g. with phone photos of the request QR frames:

    python tests/emulator.py --seed-words "abandon ... about" photo1.jpg photo2.jpg ...

scans the images from the main menu, approves the Cashu request and writes
every captured screen to ./emulator-screens.
"""

import sys
import threading
import time
from types import ModuleType
from unittest.mock import MagicMock

# --- Stub Raspberry Pi hardware modules (as the screenshot generator does)
for name in ("RPi", "RPi.GPIO", "spidev", "seedsigner.hardware.microsd",
             "seedsigner.hardware.displays.st7789_mpy", "seedsigner.hardware.displays.ili9341"):
    sys.modules[name] = MagicMock()

_picamera = ModuleType("picamera")
class PiCameraError(Exception):
    pass
_picamera.PiCameraError = PiCameraError
sys.modules["picamera"] = _picamera


class FakeVideoStream:
    """Stands in for seedsigner.hardware.pivideostream.PiVideoStream."""
    images = []      # PIL images to show the camera, in order
    fps = 5.0        # how fast the "laptop screen" advances to the next image

    def __init__(self, resolution=(480, 480), framerate=6, format="rgb", **kwargs):
        import numpy as np
        w, h = resolution
        self.frames = [np.asarray(img.convert("RGB").resize((w, h))) for img in FakeVideoStream.images]
        self.t0 = None

    def start(self):
        self.t0 = time.time()
        return self

    def read(self):
        if not self.frames:
            return None
        time.sleep(1 / 12)  # a real camera delivers frames at a limited rate
        return self.frames[int((time.time() - self.t0) * FakeVideoStream.fps) % len(self.frames)]

    def stop(self):
        pass


_pivideostream = ModuleType("seedsigner.hardware.pivideostream")
_pivideostream.PiVideoStream = FakeVideoStream
sys.modules["seedsigner.hardware.pivideostream"] = _pivideostream


from seedsigner.controller import Controller, StopFlowBasedTest                     # noqa: E402
from seedsigner.gui.renderer import Renderer                                         # noqa: E402
from seedsigner.hardware import buttons as hw_buttons                                # noqa: E402
from seedsigner.hardware.buttons import HardwareButtons, HardwareButtonsConstants    # noqa: E402
from seedsigner.hardware.displays import display_driver                              # noqa: E402
from seedsigner.hardware.displays.display_driver import BaseDisplayDriver            # noqa: E402


class FakeDisplay(BaseDisplayDriver):
    def __init__(self, width=240, height=240):
        self._width, self._height = width, height
        self.last = None
        self.lock = threading.Lock()

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    def show_image(self, image, x_start: int = 0, y_start: int = 0):
        with self.lock:
            self.last = image.copy()

    def cleanup(self):
        pass


class EmulatorFailure(Exception):
    pass


class ScriptedButtons:
    """Replaces HardwareButtons. Each wait_for() consumes the next scripted press."""

    def __init__(self, display: FakeDisplay, presses, settle_s=0.4):
        self.display = display
        self.presses = list(presses)   # [(key, label), ...]
        self.settle_s = settle_s
        self.screens = []              # [(label, PIL image), ...] captured before each press
        self.override_ind = False
        self.last_input_time = int(time.time() * 1000)

    def wait_for(self, keys=[]) -> int:
        # A press is (key, label) or (key, label, seconds to look at the screen first)
        step = self.presses[0] if self.presses else None
        time.sleep(step[2] if step and len(step) > 2 else self.settle_s)  # let rendering threads draw
        with self.display.lock:
            shot = self.display.last.copy() if self.display.last else None
        if not self.presses:
            self.screens.append(("end", shot))
            raise StopFlowBasedTest()
        key, label = self.presses.pop(0)[:2]
        self.screens.append((label, shot))
        if key not in keys:
            raise EmulatorFailure(f"script pressed {key} at '{label}' but the screen only accepts {keys}")
        return key

    def check_for_low(self, key: int = None, keys=None) -> bool:
        return False                   # nobody presses back during a scan

    def has_any_input(self) -> bool:
        return False

    def update_last_input_time(self):
        self.last_input_time = int(time.time() * 1000)

    def trigger_override(self) -> bool:
        self.override_ind = True
        return True


class Emulator:
    def __init__(self, camera_images, presses, seed_mnemonics=(), fps=5.0):
        from seedsigner.models.seed import Seed
        FakeVideoStream.images = list(camera_images)
        FakeVideoStream.fps = fps

        self.display = FakeDisplay()
        display_driver.DisplayDriverFactory.instantiate_display_driver = classmethod(
            lambda cls, display_type=None, width=240, height=240: self.display)
        self.buttons = ScriptedButtons(self.display, presses)
        HardwareButtons.get_instance = classmethod(lambda cls: self.buttons)
        HardwareButtons._instance = self.buttons

        Renderer._instance = None
        Controller.reset_instance()  # also configures a fresh Renderer on our FakeDisplay
        self.controller = Controller.get_instance()
        for words in seed_mnemonics:
            self.controller.storage.set_pending_seed(Seed(mnemonic=words.split()))
            self.controller.storage.finalize_pending_seed()

    def run(self, timeout_s=120):
        """Run the main loop until the script is used up. Returns the captured screens."""
        error = []

        def main():
            try:
                self.controller.start()
            except BaseException as e:  # surface failures from the controller thread
                error.append(e)

        t = threading.Thread(target=main, daemon=True)
        t.start()
        t.join(timeout_s)
        if t.is_alive():
            raise EmulatorFailure(f"still running after {timeout_s}s; screens so far: "
                                  f"{[label for label, _ in self.buttons.screens]}")
        if error:
            raise error[0]
        return self.buttons.screens


K = HardwareButtonsConstants
SCRIPTS = {
    # Home -> Scan -> pick the first loaded seed -> review -> Approve -> signature QR
    "approve": [
        (K.KEY_PRESS, "main menu: Scan"),
        (K.KEY_PRESS, "select signer: first seed"),
        (K.KEY_PRESS, "review 1/2: Next"),
        (K.KEY_PRESS, "review 2/2: Approve"),
        (K.KEY_PRESS, "signature QR: done", 2.0),  # the brightness tip covers the QR for 1.2 s
    ],
    # The device refuses the request before review and shows why
    "reject": [
        (K.KEY_PRESS, "main menu: Scan"),
        (K.KEY_PRESS, "select signer: first seed"),
        (K.KEY_PRESS, "rejected: OK"),
    ],
}


def read_qr_text(screen) -> str | None:
    """The QR string shown on an emulated screen, if any."""
    from PIL import Image, ImageOps
    from seedsigner.models.decode_qr import DecodeQR
    # The QR is drawn dim (QR brightness setting); a camera's exposure would compensate.
    img = ImageOps.autocontrast(screen.convert("L"))
    img = img.resize((img.width * 3, img.height * 3), Image.Resampling.NEAREST)
    text = DecodeQR.extract_qr_data(img.convert("RGB"), is_binary=True)
    return text.decode() if isinstance(text, bytes) else text


if __name__ == "__main__":
    import argparse
    import json
    import os
    from PIL import Image

    ap = argparse.ArgumentParser(description="Scan request QR images in the emulator and act on the Cashu request")
    ap.add_argument("images", nargs="+", help="photos or saved images of the request QR frames, in order")
    ap.add_argument("--seed-words", required=True, help="seed to load into memory before starting")
    ap.add_argument("--script", choices=sorted(SCRIPTS), default="approve")
    ap.add_argument("--fps", type=float, default=5.0, help="how fast the images cycle, like the host's animation")
    ap.add_argument("--out", default="emulator-screens", help="where to save every captured screen")
    args = ap.parse_args()

    screens = Emulator([Image.open(p) for p in args.images], SCRIPTS[args.script],
                       seed_mnemonics=[args.seed_words], fps=args.fps).run()
    os.makedirs(args.out, exist_ok=True)
    for i, (label, img) in enumerate(screens):
        if img is not None:
            name = "".join(c if c.isalnum() else "_" for c in label.split(":")[0])
            img.convert("RGB").save(os.path.join(args.out, f"{i:02d}_{name}.png"))
    shots = dict(screens)
    sig_screen = shots.get("signature QR: done")
    print(json.dumps({
        "screens": [label for label, _ in screens],
        "signature_qr": read_qr_text(sig_screen) if sig_screen is not None else None,
        "saved_to": os.path.abspath(args.out),
    }))
