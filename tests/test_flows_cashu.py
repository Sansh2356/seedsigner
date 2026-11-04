from unittest.mock import patch

from base import FlowTest, FlowStep
from cashu_helpers import MNEMONIC, frames, make_request, read_ur_bytes

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
        from seedsigner.nutsigner.device import summary_lines
        pages = cashu_views._review_pages(summary_lines(review))
        assert pages[0] == ["Send 21 sat", "21 as bearer token"]
        assert pages[1][0] == "Change 43 sat" and pages[1][1].startswith("Fee 0 sat")
        assert len(pages) == 2


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

    # a ur:bytes payload that isn't a Cashu request is not mistaken for one
    d = DecodeQR()
    for f in frames(b"policy: 2 of 3\nformat: P2WSH\nderivation: m/48'/0'/0'/2'\n"):
        d.add_data(f)
    assert not d.is_cashu_request
