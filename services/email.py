import os

import resend


resend.api_key = os.environ["RESEND_API_KEY"]

RESEND_FROM = os.environ["RESEND_FROM"]
RESEND_VERIFICATION_TEMPLATE_ID = os.environ[
    "RESEND_VERIFICATION_TEMPLATE_ID"
]
RESEND_WELCOME_TEMPLATE_ID = os.environ[
    "RESEND_WELCOME_TEMPLATE_ID"
]


async def send_verification_email(
    email: str,
    otp: str,
    user_name: str,
):
    params: resend.Emails.SendParams = {
        "from": RESEND_FROM,
        "to": [email],
        "reply_to": "wallflower-beauty@gmail.com",
        "template": {
            "id": RESEND_VERIFICATION_TEMPLATE_ID,
            "variables": {
                "YOUR_NAME": user_name,
                "OTP": otp,
            },
        },
    }

    return await resend.Emails.send_async(params)

async def send_welcome_email(
    email: str,
    user_name: str,
):
    params: resend.Emails.SendParams = {
        "from": RESEND_FROM,
        "to": [email],
        "reply_to": "wallflower-beauty@gmail.com",
        "template": {
            "id": RESEND_WELCOME_TEMPLATE_ID,
            "variables": {
                "YOUR_NAME": user_name,
            },
        },
    }

    return await resend.Emails.send_async(params)