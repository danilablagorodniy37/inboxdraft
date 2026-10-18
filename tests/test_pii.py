from inboxdraft import pii

SAMPLE = """Hi there,

It's Sarah Patel here. You can reach me on 07700 900123 or +44 (0)20 7946 0958,
or email sarah.patel84@example.com. My NI number is QQ 12 34 56 C and my UTR is 12345 67890.
I live at 22 Hillside Road, Guildford GU4 8AB.

Many thanks,
Sarah Patel
"""


def test_scrubs_every_kind_of_detail() -> None:
    scrubber = pii.Scrubber(from_name="Sarah Patel", from_email="sarah.patel84@example.com")
    out = scrubber.scrub(SAMPLE)
    for leaked in (
        "Sarah",
        "Patel",
        "07700 900123",
        "7946 0958",
        "sarah.patel84@example.com",
        "QQ 12 34 56 C",
        "12345 67890",
        "22 Hillside Road",
        "GU4 8AB",
    ):
        assert leaked not in out, f"{leaked!r} leaked: {out}"
    assert "[NAME_1] [NAME_2]" in out
    assert "[PHONE_1]" in out and "[PHONE_2]" in out
    assert "[EMAIL_1]" in out
    assert "[NINO_1]" in out
    assert "[UTR_1]" in out
    assert "[ADDRESS_1]" in out
    assert "[POSTCODE_1]" in out


def test_same_value_gets_same_token_across_subject_and_body() -> None:
    scrubber = pii.Scrubber(from_name="Tom Okafor", from_email="t.okafor@example.org")
    subject = scrubber.scrub("Message from Tom Okafor")
    body = scrubber.scrub("Hi, Tom Okafor again, t.okafor@example.org")
    assert subject == "Message from [NAME_1] [NAME_2]"
    assert body == "Hi, [NAME_1] [NAME_2] again, [EMAIL_1]"
    assert scrubber.mapping["[EMAIL_1]"] == "t.okafor@example.org"


def test_restore_round_trips_and_tolerates_missing_brackets() -> None:
    scrubber = pii.Scrubber(from_name="Nina Petrova", from_email="nina@example.com")
    scrubbed = scrubber.scrub("Call me on 07700 900789. Nina")
    draft = "Dear [NAME_1],\n\nWe will call you on PHONE_1 as requested.\n\nRegards"
    restored = pii.restore(draft, scrubber.mapping)
    assert restored.startswith("Dear Nina,")
    assert "07700 900789" in restored
    assert "[" not in restored
    assert scrubbed != restored


def test_signature_names_are_tokenised_even_when_unknown() -> None:
    scrubber = pii.Scrubber(from_name=None, from_email="noreply@example.com")
    out = scrubber.scrub(
        "Can you send me the price list?\n\nThanks,\nMegan Ford\nOperations Manager"
    )
    assert "Megan" not in out and "Ford" not in out
    assert "[NAME_1] [NAME_2]" in out
    # the job title line is not a name line: it has 2 capitalised words and stays as is
    assert "Operations Manager" in out


def test_firm_words_and_small_numbers_are_left_alone() -> None:
    scrubber = pii.Scrubber(from_name="Jack Thompson", from_email="jack@example.com")
    out = scrubber.scrub("Is the fee still £180 for 2 properties? Thanks, The Team")
    assert "£180" in out and "2 properties" in out
    assert "The Team" in out


def test_inline_signoff_and_known_surname_lines() -> None:
    scrubber = pii.Scrubber(from_name="Growth Partners", from_email="outreach@example.com")
    assert scrubber.scrub("Can I send you a free audit? Best, Mike").endswith("Best, [NAME_3]")
    scrubber = pii.Scrubber(from_name="Daniel Hughes", from_email="dan@example.com")
    out = scrubber.scrub("What do you need from us?\n\nDan Hughes\nHughes Joinery Ltd")
    assert "Dan" not in out and "Hughes" not in out
    assert "[NAME_3] [NAME_2]\n[NAME_2] [NAME_4] Ltd" in out


def test_mapping_is_not_part_of_the_scrubbed_text() -> None:
    scrubber = pii.Scrubber(from_name="Grace Li", from_email="grace.li@example.com")
    out = scrubber.scrub("grace.li@example.com")
    assert out == "[EMAIL_1]"
    assert "grace.li@example.com" not in out
