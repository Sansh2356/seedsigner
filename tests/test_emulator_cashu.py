"""End to end in the headless emulator (tests/emulator.py): real Controller,
Views, Screens, Camera and DecodeQR; camera input from saved QR images.

The host's animated request QR is rendered to image files and played to the
emulated camera; scripted button presses go Scan -> select signer -> review ->
Approve; the signature QR is read back off the emulated display and verified.

The emulator runs in a subprocess because tests/base.py (imported by the flow
tests in the same pytest process) replaces the renderer, buttons and camera
stream with mocks for the whole process.
"""

import json
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageFilter
from urtypes.bytes import Bytes

from cashu_helpers import MNEMONIC, make_request, read_ur_bytes
from seedsigner.helpers.qr import QR
from seedsigner.helpers.ur2.ur import UR
from seedsigner.helpers.ur2.ur_encoder import UREncoder
from seedsigner.nutsigner import crypto, wire
from seedsigner.nutsigner.device import DeviceSigner

EMULATOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emulator.py")


def save_camera_images(data: bytes, folder: str, fragment_len: int = 120) -> list[str]:
    """The laptop screen as the camera sees it: each animated frame, framed and slightly soft."""
    enc = UREncoder(UR("bytes", Bytes(data).to_cbor()), fragment_len)
    paths = []
    for i in range(enc.fountain_encoder.seq_len()):
        # white background, like the host's window (qrimage defaults to SeedSigner's dark grey)
        code = QR().qrimage(enc.next_part().upper(), width=400, height=400, border=2,
                            background_color="#ffffff").convert("L")
        frame = Image.new("L", (480, 480), 235)  # off-white screen around the code
        frame.paste(code, (40, 40))
        path = os.path.join(folder, f"frame{i:02d}.png")
        frame.filter(ImageFilter.GaussianBlur(1)).convert("RGB").save(path)
        paths.append(path)
    return paths


def run_emulator(request: bytes, script: str) -> dict:
    tmp = tempfile.mkdtemp()
    images = save_camera_images(request, tmp)
    out = os.environ.get("EMULATOR_SCREENS") or os.path.join(tmp, "screens")
    proc = subprocess.run(
        [sys.executable, EMULATOR, "--seed-words", MNEMONIC, "--script", script, "--out", out, *images],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_scan_request_images_approve_and_read_signature_off_screen():
    pubkey = DeviceSigner.from_mnemonic(MNEMONIC).pubkey_hex
    request = make_request(pubkey)

    result = run_emulator(request, "approve")

    assert result["screens"] == ["main menu: Scan", "select signer: first seed", "review 1/2: Next",
                                 "review 2/2: Approve", "signature QR: done", "end"]
    assert result["signature_qr"], "no QR could be read off the signature screen"
    sig = wire.decode_signature(read_ur_bytes(result["signature_qr"]))
    req = wire.decode_request(request)
    assert sig["pubkey"] == pubkey
    assert crypto.schnorr_verify(pubkey, crypto.sig_all_message(req["inputs"], req["outputs"]), sig["signature"])


def test_lying_request_is_rejected_on_screen():
    pubkey = DeviceSigner.from_mnemonic(MNEMONIC).pubkey_hex
    result = run_emulator(make_request(pubkey, lie=True), "reject")
    # Had the device not rejected it, the screen after "select signer" would be a
    # review page whose buttons don't match the script, and the run would fail.
    assert result["screens"] == ["main menu: Scan", "select signer: first seed", "rejected: OK", "end"]
    assert result["signature_qr"] is None
