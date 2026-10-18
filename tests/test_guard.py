from inboxdraft.guard import figures, unsupported_figures

SOURCES = [
    "Basic return (employment income, pensions, savings interest, dividends): from £180.",
    "Please give us at least 24 hours' notice. A missed meeting is charged at £40.",
    "The office is open Monday to Friday from 9:00 to 17:30.",
]


def test_supported_figures_pass() -> None:
    draft = "Our basic return is from £180 plus VAT [1]. We open at 9:00 and ask for 24 hours' notice [2][3]."
    assert unsupported_figures(draft, SOURCES) == []


def test_invented_price_is_caught() -> None:
    draft = "Our basic return is £150 and a landlord return is £240."
    assert unsupported_figures(draft, SOURCES) == ["£150", "£240"]


def test_numbers_from_the_enquiry_are_allowed() -> None:
    draft = "Thank you for sending the 80 transactions; the fee is from £180."
    assert unsupported_figures(draft, SOURCES, enquiry="roughly 80 transactions a month") == []
    assert unsupported_figures(draft, SOURCES, enquiry="") == ["80"]


def test_small_bare_numbers_citations_and_tokens_are_ignored() -> None:
    draft = "Dear [NAME_1], 2 of our 3 accountants are available [1]. Call [PHONE_1]."
    assert unsupported_figures(draft, SOURCES) == []
    assert figures(draft) == {}


def test_percent_and_time_formats() -> None:
    draft = "VAT at 20% is added. We close at 17.30."
    assert unsupported_figures(draft, SOURCES) == ["20%"]
    assert unsupported_figures(draft, [*SOURCES, "VAT at 20 % applies"]) == []
