from unittest.mock import patch

from base import FlowTest, FlowStep
from cashu_helpers import (INVOICE_10, MNEMONIC, frames, make_melt_request, make_receive_request,
                           make_request, read_ur_bytes)

from seedsigner.models import encode_qr
from seedsigner.models.decode_qr import DecodeQR
from seedsigner.models.seed import Seed
from seedsigner.nutsigner import crypto, wire
from seedsigner.nutsigner.device import DeviceSigner
from seedsigner.views import cashu_views, scan_views, tools_views
from seedsigner.views.view import ErrorView, MainMenuView


class TestCashuFlows(FlowTest):
    def setup_method(self):
        super().setup_method()
        self.controller.storage.set_pending_seed(Seed(mnemonic=MNEMONIC.split()))
        self.controller.storage.finalize_pending_seed()
        self.pubkey = DeviceSigner.from_mnemonic(MNEMONIC).pubkey_hex


    def scan(self, data: bytes):
        def load(view: scan_views.ScanView):
            for f in frames(data):
                view.decoder.add_data(f)
        return load


    def test_scan_review_approve_shows_valid_signature(self):
        request = make_request(self.pubkey)
        shown = []
        real = encode_qr.GenericStaticQrEncoder
        with patch.object(encode_qr, "GenericStaticQrEncoder", side_effect=lambda data: shown.append(data) or real(data=data)):
            self.run_sequence([
                FlowStep(MainMenuView, button_data_selection=MainMenuView.SCAN),
                FlowStep(scan_views.ScanView, before_run=self.scan(request)),
                FlowStep(cashu_views.CashuSelectSeedView, screen_return_value=0),
                FlowStep(cashu_views.CashuReviewView, button_data_selection=cashu_views.CashuReviewView.NEXT),
                FlowStep(cashu_views.CashuReviewView, button_data_selection=cashu_views.CashuReviewView.APPROVE),
                FlowStep(cashu_views.CashuSignatureQRView),
                FlowStep(MainMenuView),
            ])

        sig = wire.decode_signature(read_ur_bytes(shown[0]))
        req = wire.decode_request(request)
        assert sig["pubkey"] == self.pubkey
        assert crypto.schnorr_verify(self.pubkey, crypto.sig_all_message(req["inputs"], req["outputs"]), sig["signature"])


    def test_review_pages(self):
        review = DeviceSigner.from_mnemonic(MNEMONIC).review(wire.decode_request(make_request(self.pubkey)))
        pages, first_warning = cashu_views._review_pages(review)
        assert pages[0] == ["Send 21 sat", "21 as bearer token"]
        assert pages[1][0] == "Change 43 sat" and pages[1][1].startswith("Fee 0 sat")
        assert (len(pages), first_warning) == (2, 2)


    def test_review_pages_melt_and_receive(self):
        dev = DeviceSigner.from_mnemonic(MNEMONIC)
        pages, first_warning = cashu_views._review_pages(dev.review(wire.decode_request(make_melt_request(self.pubkey))))
        assert pages == [["Pay 10 sat (Lightning)", "Max fee 2 sat", "Mint testnut.cashu.sp"],
                         ["Up to 16 sat at risk", "Payee not verified"]]
        assert first_warning == 1  # the at-risk page is a warning, and carries Approve / Reject
        pages, _ = cashu_views._review_pages(dev.review(wire.decode_request(make_receive_request(self.pubkey))))
        assert pages == [["Receive 21 sat", "Fee 1 sat", "Mint testnut.cashu.sp"]]


    def approve(self, request: bytes, pages: int) -> dict:
        """Scan -> review (Next on every page but the last) -> Approve; the signature shown."""
        shown = []
        real = encode_qr.GenericStaticQrEncoder
        review = [FlowStep(cashu_views.CashuReviewView, button_data_selection=cashu_views.CashuReviewView.NEXT)
                  for _ in range(pages - 1)]
        with patch.object(encode_qr, "GenericStaticQrEncoder", side_effect=lambda data: shown.append(data) or real(data=data)):
            self.run_sequence([
                FlowStep(MainMenuView, button_data_selection=MainMenuView.SCAN),
                FlowStep(scan_views.ScanView, before_run=self.scan(request)),
                FlowStep(cashu_views.CashuSelectSeedView, screen_return_value=0),
                *review,
                FlowStep(cashu_views.CashuReviewView, button_data_selection=cashu_views.CashuReviewView.APPROVE),
                FlowStep(cashu_views.CashuSignatureQRView),
                FlowStep(MainMenuView),
            ])
        sig = wire.decode_signature(read_ur_bytes(shown[0]))
        assert sig["pubkey"] == self.pubkey
        return sig


    def test_melt_scan_review_approve_signs_over_the_quote(self):
        request = make_melt_request(self.pubkey)
        sig = self.approve(request, pages=2)
        req = wire.decode_request(request)
        assert req["invoice"] == INVOICE_10
        msg = crypto.sig_all_message(req["inputs"], req["outputs"], req["quote"])
        assert crypto.schnorr_verify(self.pubkey, msg, sig["signature"])


    def test_receive_scan_review_approve(self):
        request = make_receive_request(self.pubkey)
        sig = self.approve(request, pages=1)
        req = wire.decode_request(request)
        assert crypto.schnorr_verify(self.pubkey, crypto.sig_all_message(req["inputs"], req["outputs"]), sig["signature"])


    def test_melt_reject_on_review(self):
        self.run_sequence([
            FlowStep(MainMenuView, button_data_selection=MainMenuView.SCAN),
            FlowStep(scan_views.ScanView, before_run=self.scan(make_melt_request(self.pubkey))),
            FlowStep(cashu_views.CashuSelectSeedView, screen_return_value=0),
            FlowStep(cashu_views.CashuReviewView, button_data_selection=cashu_views.CashuReviewView.NEXT),
            FlowStep(cashu_views.CashuReviewView, button_data_selection=cashu_views.CashuReviewView.REJECT),
            FlowStep(MainMenuView),
        ])


    def test_tampered_melt_is_rejected_with_reason(self):
        self.run_sequence([
            FlowStep(MainMenuView, button_data_selection=MainMenuView.SCAN),
            FlowStep(scan_views.ScanView, before_run=self.scan(make_melt_request(self.pubkey, tamper=True))),
            FlowStep(cashu_views.CashuSelectSeedView, screen_return_value=0),
            FlowStep(cashu_views.CashuReviewView, is_redirect=True),
            FlowStep(ErrorView, screen_return_value=0),
            FlowStep(MainMenuView),
        ])


    def test_unverified_outputs_get_a_warning_page(self):
        self.run_sequence([
            FlowStep(tools_views.ToolsMenuView, button_data_selection=tools_views.ToolsMenuView.CASHU),
            FlowStep(cashu_views.CashuMenuView, button_data_selection=cashu_views.CashuMenuView.SCAN),
            FlowStep(scan_views.ScanCashuRequestView, before_run=self.scan(make_request(self.pubkey, reveal_all=False))),
            FlowStep(cashu_views.CashuSelectSeedView, screen_return_value=0),
            FlowStep(cashu_views.CashuReviewView, button_data_selection=cashu_views.CashuReviewView.NEXT),
            FlowStep(cashu_views.CashuReviewView, button_data_selection=cashu_views.CashuReviewView.NEXT),
            FlowStep(cashu_views.CashuReviewView, button_data_selection=cashu_views.CashuReviewView.REJECT),
            FlowStep(MainMenuView),
        ])


    def test_lying_host_is_rejected_with_reason(self):
        self.run_sequence([
            FlowStep(MainMenuView, button_data_selection=MainMenuView.SCAN),
            FlowStep(scan_views.ScanView, before_run=self.scan(make_request(self.pubkey, lie=True))),
            FlowStep(cashu_views.CashuSelectSeedView, screen_return_value=0),
            FlowStep(cashu_views.CashuReviewView, is_redirect=True),
            FlowStep(ErrorView, screen_return_value=0),
            FlowStep(MainMenuView),
        ])


    def test_request_for_another_key_is_rejected(self):
        other = DeviceSigner.from_mnemonic("legal winner thank year wave sausage worth useful legal winner thank yellow")
        self.run_sequence([
            FlowStep(MainMenuView, button_data_selection=MainMenuView.SCAN),
            FlowStep(scan_views.ScanView, before_run=self.scan(make_request(other.pubkey_hex))),
            FlowStep(cashu_views.CashuSelectSeedView, screen_return_value=0),
            FlowStep(cashu_views.CashuReviewView, is_redirect=True),
            FlowStep(ErrorView, screen_return_value=0),
            FlowStep(MainMenuView),
        ])


    def test_show_pubkey(self):
        shown = []
        real = encode_qr.GenericStaticQrEncoder
        with patch.object(encode_qr, "GenericStaticQrEncoder", side_effect=lambda data: shown.append(data) or real(data=data)):
            self.run_sequence([
                FlowStep(tools_views.ToolsMenuView, button_data_selection=tools_views.ToolsMenuView.CASHU),
                FlowStep(cashu_views.CashuMenuView, button_data_selection=cashu_views.CashuMenuView.PUBKEY),
                FlowStep(cashu_views.CashuSelectSeedView, screen_return_value=0),
                FlowStep(cashu_views.CashuPubkeyView),
                FlowStep(cashu_views.CashuSelectSeedView),
            ])
        assert shown == [self.pubkey.upper()]


    def test_no_seed_loaded(self):
        self.controller.storage.seeds.clear()
        self.run_sequence([
            FlowStep(MainMenuView, button_data_selection=MainMenuView.SCAN),
            FlowStep(scan_views.ScanView, before_run=self.scan(make_request(self.pubkey))),
            FlowStep(cashu_views.CashuSelectSeedView, is_redirect=True),
            FlowStep(ErrorView, screen_return_value=0),
            FlowStep(MainMenuView),
        ])



def test_decoder_recognises_cashu_request_without_crashing_on_binary():
    data = make_request(DeviceSigner.from_mnemonic(MNEMONIC).pubkey_hex)
    d = DecodeQR()
    for f in frames(data):
        d.add_data(f)
    assert d.is_complete
    assert d.is_cashu_request and d.get_cashu_request_bytes() == data
    assert d.is_wallet_descriptor is False  # used to raise UnicodeDecodeError on binary ur:bytes

    melt = make_melt_request(DeviceSigner.from_mnemonic(MNEMONIC).pubkey_hex)
    d = DecodeQR()
    for f in frames(melt):
        d.add_data(f)
    assert d.is_cashu_request and d.get_cashu_request_bytes() == melt

    # a ur:bytes payload that isn't a Cashu request is not mistaken for one
    d = DecodeQR()
    for f in frames(b"policy: 2 of 3\nformat: P2WSH\nderivation: m/48'/0'/0'/2'\n"):
        d.add_data(f)
    assert not d.is_cashu_request
