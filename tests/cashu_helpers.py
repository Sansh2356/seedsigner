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


def frames(data: bytes, fragment_len: int = 30) -> list[str]:
    """The animated ur:bytes QR frames the host would show."""
    enc = UREncoder(UR("bytes", Bytes(data).to_cbor()), fragment_len)
    return [enc.next_part().upper() for _ in range(enc.fountain_encoder.seq_len())]


def read_ur_bytes(qr: str) -> bytes:
    d = URDecoder()
    d.receive_part(qr)
    return Bytes.from_cbor(d.result_message().cbor).data
