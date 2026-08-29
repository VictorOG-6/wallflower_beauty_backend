import hashlib
import hmac
import unittest
from unittest.mock import patch

import httpx

from services.paystack import PaystackError, PaystackService, naira_to_kobo


class PaystackServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = PaystackService(
            secret_key="test-secret",
            base_url="https://api.paystack.co",
            callback_url="https://shop.test/payment/callback",
        )

    def test_naira_is_converted_to_kobo(self):
        self.assertEqual(naira_to_kobo(5_000), 500_000)
        with self.assertRaises(ValueError):
            naira_to_kobo(0)

    def test_webhook_signature_uses_raw_body(self):
        body = b'{"event":"charge.success","data":{"reference":"ord-1"}}'
        signature = hmac.new(
            b"test-secret",
            body,
            hashlib.sha512,
        ).hexdigest()

        self.assertTrue(
            self.service.verify_webhook_signature(body, signature)
        )
        self.assertFalse(
            self.service.verify_webhook_signature(body + b" ", signature)
        )

    @patch("services.paystack.httpx.request")
    def test_initialize_transaction_is_server_owned(self, request):
        request.return_value = httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "authorization_url": "https://checkout.paystack.com/code",
                    "access_code": "code",
                    "reference": "ord-1-attempt",
                },
            },
        )

        result = self.service.initialize_transaction(
            email="buyer@example.com",
            amount_kobo=500_000,
            reference="ord-1-attempt",
            order_id="order-uuid",
        )

        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["amount"], 500_000)
        self.assertEqual(payload["currency"], "NGN")
        self.assertEqual(payload["reference"], "ord-1-attempt")
        self.assertEqual(payload["metadata"]["order_id"], "order-uuid")
        self.assertEqual(
            payload["callback_url"],
            "https://shop.test/payment/callback",
        )
        self.assertEqual(result["access_code"], "code")

    @patch("services.paystack.httpx.request")
    def test_provider_errors_do_not_look_successful(self, request):
        request.return_value = httpx.Response(
            400,
            json={"status": False, "message": "Invalid amount"},
        )

        with self.assertRaisesRegex(PaystackError, "Invalid amount"):
            self.service.verify_transaction("bad-reference")

    @patch("services.paystack.httpx.request")
    def test_refund_request_is_full_and_uses_transaction_reference(self, request):
        request.return_value = httpx.Response(
            200,
            json={
                "status": True,
                "data": {"status": "pending", "id": 42},
            },
        )

        self.service.create_full_refund(
            transaction_reference="ord-1-attempt",
            merchant_note="Customer cancellation",
        )

        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["transaction"], "ord-1-attempt")
        self.assertNotIn("amount", payload)


if __name__ == "__main__":
    unittest.main()
