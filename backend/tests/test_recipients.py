import pytest

from app import recipients


@pytest.mark.parametrize("function, contact, heard, ok", [
    ("pc_send_message", "Him", ["message him hi"], False),  # was sent to a chat called "Him"
    ("pc_call", "her", ["call her"], False),
    ("pc_send_message", "Tanishka", ["whatsapp them that I'm late"], False),  # a name nobody said
    ("pc_send_message", "Rahul", ["send a whatsapp to Rahul saying hi"], True),
    ("pc_send_message", "Rahul Sharma", ["message Rahul: lunch?", "The closest are 'Rahul Sharma', 'Rahul Verma'", "Sharma"], True),
    ("pc_send_message", "Mrunal", ["message Mronal saying hi"], True),  # a misheard spelling, corrected
    ("pc_call", "Piyush", ["call to Piyush"], True),
    ("pc_send_message", "राहुल", ["राहुल को हाय भेजो"], True),
    ("pc_send_message", "Rahul", ["राहुल को हाय भेजो"], True),  # said in Hindi, written in Latin letters
    ("pc_send_message", "Priya", ["text my wife that I'm on my way"], True),  # Custodian may know who that is
    ("pc_send_message", "+91 98765 43210", ["send a whatsapp to +91 98765 43210 saying hi"], True),
    ("pc_send_message", "", ["send it"], False),
    ("pc_open_chat", "someone", ["open someone's chat"], False),
    ("pc_open", "anything", ["open anything"], True),  # not a message or call
])
def test_a_message_or_call_goes_only_to_someone_named(function, contact, heard, ok):
    why = recipients.check(function, {"contact": contact}, heard)
    assert (why is None) is ok, why
    if not ok:
        assert "ask" in why
