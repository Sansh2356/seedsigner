"""Cashu test data shared by the flow tests and the emulator test. Imports no mocks."""

import os

from urtypes.bytes import Bytes

from seedsigner.helpers.ur2.ur import UR
from seedsigner.helpers.ur2.ur_decoder import URDecoder
from seedsigner.helpers.ur2.ur_encoder import UREncoder
from seedsigner.nutsigner import crypto, curve, wire

MNEMONIC = "abandon " * 11 + "about"
KEYSET_ID = "01" + "ab" * 32  # testnut-sized (v2) keyset id
G_HEX = curve.serialize(curve.G).hex()


def make_request(pubkey: str, reveal_all: bool = True, lie: bool = False) -> bytes:
    """A 1-input swap: 21 sat to a bearer token, 43 sat change back to `pubkey`."""
    outputs, reveals = [], []
    for amount, secret in [(a, os.urandom(32).hex()) for a in (1, 4, 16)] + \
                          [(a, crypto.make_p2pk_secret(pubkey)) for a in (1, 2, 8, 32)]:
        r = crypto.random_scalar()
        outputs.append({"amount": amount, "id": KEYSET_ID, "B_": crypto.blind(secret, r)})
        reveals.append({"index": len(reveals), "secret": secret, "r": format(r, "064x")})
    if lie:  # claim a recipient output is change
        reveals[0]["secret"] = crypto.make_p2pk_secret(pubkey)
    req = {
        "v": 1, "type": "swap", "mint": "https://testnut.cashu.space", "unit": "sat",
        "inputs": [{"amount": 64, "id": KEYSET_ID, "secret": crypto.make_p2pk_secret(pubkey), "C": G_HEX}],
        "outputs": outputs,
        "reveals": reveals if reveal_all else [],
    }
    return wire.encode_request(req)


# A real, signed 10 sat mainnet invoice (from NutSigner's tests.fake_mint.make_invoice)
INVOICE_10 = ("lnbc100n1p40tu94pp5p7m3feh2skx73hr4ldnq7rcljjhgz2sj3k28p0whtf66mymlqtnssp57awphhj3nl75rheq9p72"
              "k2238cpm6c2lr6wdvkdkk9f7gzsz589qdqq9qrsgqcqzys2uxcv5azg4mfcljwuv3j53dd6zu7f3ad8y3d8fqeu5y5hqnlszl3"
              "f9fnwzvwjy280zr9v5ytnpjmvqewdd0g9mfnddkz0pvs4kw53wcqg5g85j")


def _change_outputs(pubkey: str, amounts) -> tuple[list, list]:
    outputs, reveals = [], []
    for amount in amounts:
        secret, r = crypto.make_p2pk_secret(pubkey), crypto.random_scalar()
        outputs.append({"amount": amount, "id": KEYSET_ID, "B_": crypto.blind(secret, r)})
        reveals.append({"index": len(reveals), "secret": secret, "r": format(r, "064x")})
    return outputs, reveals


def make_melt_request(pubkey: str, tamper: bool = False) -> bytes:
    """Pay INVOICE_10 from one 16 sat input, fee reserve 2; 3 blank change outputs."""
    outputs, reveals = _change_outputs(pubkey, [0, 0, 0])
    req = {
        "v": 1, "type": "melt", "mint": "https://testnut.cashu.space", "unit": "sat",
        "invoice": INVOICE_10, "quote": "q" * 36, "amount": 10, "fee_reserve": 2,
        "inputs": [{"amount": 16, "id": KEYSET_ID, "secret": crypto.make_p2pk_secret(pubkey), "C": G_HEX}],
        "outputs": outputs, "reveals": reveals,
    }
    if tamper:  # the host claims a smaller payment than the invoice asks for
        req["amount"] = 9
    return wire.encode_request(req)


def make_receive_request(pubkey: str) -> bytes:
    """Swap a 21 sat token locked to `pubkey` (3 proofs) into 20 sat of change; fee 1."""
    outputs, reveals = _change_outputs(pubkey, [4, 16])
    req = {
        "v": 1, "type": "swap", "mint": "https://testnut.cashu.space", "unit": "sat",
        "inputs": [{"amount": a, "id": KEYSET_ID, "secret": crypto.make_p2pk_secret(pubkey), "C": G_HEX}
                   for a in (1, 4, 16)],
        "outputs": outputs, "reveals": reveals,
    }
    return wire.encode_request(req)


def frames(data: bytes, fragment_len: int = 30) -> list[str]:
    """The animated ur:bytes QR frames the host would show."""
    enc = UREncoder(UR("bytes", Bytes(data).to_cbor()), fragment_len)
    return [enc.next_part().upper() for _ in range(enc.fountain_encoder.seq_len())]


def read_ur_bytes(qr: str) -> bytes:
    d = URDecoder()
    d.receive_part(qr)
    return Bytes.from_cbor(d.result_message().cbor).data
