"""Cashu (NutSigner) flow: show the P2PK pubkey, review and sign swap requests.

The host wallet sends a NutSigner v2 request as an animated `ur:bytes` QR. The
device checks it with seedsigner.nutsigner (the same code as the NutSigner
CLI), shows what will happen, and on approval shows the 132-byte signature as
a single `ur:bytes` QR. Nothing is written to storage; the key is derived from
a seed already loaded in memory.
"""

import logging

from gettext import gettext as _

from seedsigner.gui.components import GUIConstants, SeedSignerIconConstants
from seedsigner.gui.screens.screen import (RET_CODE__BACK_BUTTON, ButtonListScreen, ButtonOption,
    LargeIconStatusScreen, QRDisplayScreen, WarningScreen)
from seedsigner.models.settings import SettingsConstants
from seedsigner.views.view import BackStackView, ErrorView, MainMenuView, View, Destination

logger = logging.getLogger(__name__)


def _signer(controller, seed_num: int):
    from seedsigner.nutsigner.device import DeviceSigner
    return DeviceSigner.from_seed_bytes(controller.storage.seeds[seed_num].seed_bytes)


def _rejected(reason: str, **kw) -> Destination:
    """The device refused the request: show why, then go home."""
    return Destination(ErrorView, view_args=dict(
        title=_("Cashu"),
        status_headline=_("Request rejected"),
        text=reason,
        button_text=_("OK"),
        next_destination=Destination(MainMenuView, clear_history=True),
    ), **kw)


def _review_pages(lines: list[str]) -> list[list[str]]:
    """Group summary_lines(): the send line with its recipients, then the
    change/fee/mint details, then each warning on its own page."""
    send = [lines[0]] + [l.strip() for l in lines[1:] if l.startswith(" ")]
    rest = [l for l in lines[1:] if not l.startswith(" ")]
    details = [l for l in rest if not l.startswith("!")]
    warnings = [[l.lstrip("! ")] for l in rest if l.startswith("!")]
    return [send, details] + warnings



class CashuMenuView(View):
    PUBKEY = ButtonOption("Show pubkey", SeedSignerIconConstants.FINGERPRINT)
    SCAN = ButtonOption("Scan request", SeedSignerIconConstants.SCAN)

    def run(self):
        button_data = [self.PUBKEY, self.SCAN]
        selected_menu_num = self.run_screen(
            ButtonListScreen,
            title=_("Cashu"),
            is_button_text_centered=False,
            button_data=button_data,
        )
        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)
        if button_data[selected_menu_num] == self.PUBKEY:
            return Destination(CashuSelectSeedView)
        from seedsigner.views.scan_views import ScanCashuRequestView
        return Destination(ScanCashuRequestView)



class CashuSelectSeedView(View):
    """Pick which loaded seed holds the Cashu key. With a `request`, continue to
    review it; without one, show that seed's pubkey."""

    def __init__(self, request: bytes = None):
        super().__init__()
        self.request = request

    def run(self):
        seeds = self.controller.storage.seeds
        if not seeds:
            return Destination(ErrorView, view_args=dict(
                title=_("Cashu"),
                status_headline=_("No seed loaded"),
                text=_("Load a seed from the Seeds menu first. It stays in memory only."),
                button_text=_("OK"),
            ), skip_current_view=True)

        network = self.settings.get_value(SettingsConstants.SETTING__NETWORK)
        button_data = [ButtonOption(seed.get_fingerprint(network), SeedSignerIconConstants.FINGERPRINT)
                       for seed in seeds]
        selected_menu_num = self.run_screen(
            ButtonListScreen,
            title=_("Select Signer"),
            is_button_text_centered=False,
            button_data=button_data,
        )
        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)
        if self.request is None:
            return Destination(CashuPubkeyView, view_args=dict(seed_num=selected_menu_num))
        return Destination(CashuReviewView, view_args=dict(seed_num=selected_menu_num, request=self.request))



class CashuPubkeyView(View):
    """The key ecash gets locked to, as a QR for `host_cli.py fund --pubkey`."""

    def __init__(self, seed_num: int):
        super().__init__()
        self.seed_num = seed_num

    def run(self):
        from seedsigner.models.encode_qr import GenericStaticQrEncoder
        pubkey = _signer(self.controller, self.seed_num).pubkey_hex
        # Uppercase hex fits QR alphanumeric mode; the host accepts either case.
        self.run_screen(QRDisplayScreen, qr_encoder=GenericStaticQrEncoder(data=pubkey.upper()))
        return Destination(BackStackView)



class CashuReviewView(View):
    """One page of the request summary. The last page offers Approve / Reject."""
    NEXT = ButtonOption("Next")
    APPROVE = ButtonOption("Approve", SeedSignerIconConstants.CHECK)
    REJECT = ButtonOption("Reject")

    def __init__(self, seed_num: int, request: bytes, page: int = 0):
        from seedsigner.nutsigner import wire
        from seedsigner.nutsigner.device import Rejected, summary_lines
        super().__init__()
        self.seed_num, self.request, self.page = seed_num, request, page
        try:
            review = _signer(self.controller, seed_num).review(wire.decode_request(request))
        except (Rejected, wire.WireError) as e:
            self.set_redirect(_rejected(str(e), skip_current_view=True))
            return
        self.pages = _review_pages(summary_lines(review))
        self.unverified = review["unverified"]

    def run(self):
        lines = self.pages[self.page]
        is_last = self.page == len(self.pages) - 1
        is_warning = self.page >= 2
        button_data = [self.APPROVE, self.REJECT] if is_last else [self.NEXT]
        text = "\n".join(lines[1:])
        if is_warning and self.unverified:
            text = _("{} output(s) not proven to be yours. Counted as sent.").format(self.unverified)
        selected_menu_num = self.run_screen(
            WarningScreen if is_warning else LargeIconStatusScreen,
            title=_("Review {}/{}").format(self.page + 1, len(self.pages)),
            status_icon_name=SeedSignerIconConstants.WARNING if is_warning else SeedSignerIconConstants.INFO,
            status_color=GUIConstants.WARNING_COLOR if is_warning else GUIConstants.INFO_COLOR,
            status_headline=lines[0],
            text=text,
            button_data=button_data,
            show_back_button=True,
        )
        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)
        choice = button_data[selected_menu_num]
        if choice == self.NEXT:
            return Destination(CashuReviewView, view_args=dict(
                seed_num=self.seed_num, request=self.request, page=self.page + 1))
        if choice == self.APPROVE:
            return Destination(CashuSignatureQRView, view_args=dict(seed_num=self.seed_num, request=self.request))
        return Destination(MainMenuView, clear_history=True)  # rejected: discard the request



class CashuSignatureQRView(View):
    """Sign (sign() re-runs the full review) and show the signature QR."""

    def __init__(self, seed_num: int, request: bytes):
        super().__init__()
        self.seed_num, self.request = seed_num, request

    def run(self):
        from urtypes.bytes import Bytes
        from seedsigner.helpers.ur2.ur import UR
        from seedsigner.helpers.ur2.ur_encoder import UREncoder
        from seedsigner.models.encode_qr import GenericStaticQrEncoder
        from seedsigner.nutsigner import wire
        from seedsigner.nutsigner.device import Rejected

        try:
            sig = _signer(self.controller, self.seed_num).sign(wire.decode_request(self.request))
        except (Rejected, wire.WireError) as e:
            return _rejected(str(e), clear_history=True)
        qr = UREncoder.encode(UR("bytes", Bytes(wire.encode_signature(sig)).to_cbor())).upper()
        self.run_screen(QRDisplayScreen, qr_encoder=GenericStaticQrEncoder(data=qr))
        # Done with this request; MainMenuView clears everything but in-memory seeds.
        return Destination(MainMenuView, clear_history=True)
