import pytest

from app import sensitive


@pytest.mark.parametrize(
    "text, kind",
    [
        ("my bank password is hunter2", "a password or other login secret"),
        ("the card pin is 4321", "a password or other login secret"),
        ("my UPI PIN is 1947", "a password or other login secret"),
        ("PIN: 8811", "a password or other login secret"),
        ("the OTP is 554433", "a password or other login secret"),
        ("my Aadhaar number is 1234 5678 9012", "an Aadhaar number"),
        ("aadhar: 234567890123", "an Aadhaar number"),
        ("ID 2345-6789-0123 on the form", "an Aadhaar number"),
        ("my PAN is ABCDE1234F", "a PAN"),
        ("pan abcpe1234k", "a PAN"),
        ("my bank account number is 50100234567", "a bank account number"),
        ("A/c no. 1234 5678 90", "a bank account number"),
        ("card 4111 1111 1111 1111 expires soon", "a card number"),
        ("passport number is K1234567", "a passport number"),
        ("SSN 123-45-6789", "a Social Security number"),
        ("key sk-proj-abcdefghijklmnop1234", "an API key or access token"),
        ("token ghp_abcdefghijklmnopqrstuvwx", "an API key or access token"),
    ],
)
def test_secrets_are_recognised(text, kind):
    assert sensitive.find(text) == kind


@pytest.mark.parametrize(
    "text",
    [
        "Partner's name is Priya.",
        "Priya's number is 98765 43210",
        "call me on +91 98765 43210",
        "our pin code is 560038",
        "the office pincode is 400001",
        "Passport expires in June 2028.",
        "My passport is with Priya",
        "Bank branch is HDFC Indiranagar.",
        "The car service cost 8,400 rupees",
        "Landlord is Mr. Sharma and rent is due on the 5th.",
        "the meeting is on 2026-10-15 at 15:30",
        "my account balance is low this month",
        "I parked on level 3 at pillar B7",
        "order 112233 arrives on Friday",
    ],
)
def test_ordinary_facts_are_not_secrets(text):
    assert sensitive.find(text) is None
