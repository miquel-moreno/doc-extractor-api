import pytest

from doc_extractor_api.services.tax_id import TaxIdKind, normalize_tax_id, validate_tax_id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12345678z", "12345678Z"),
        ("12.345.678-Z", "12345678Z"),
        (" ES B12345674 ", "B12345674"),
        ("es-x1234567l", "X1234567L"),
    ],
)
def test_normalize_removes_separators_and_country_prefix(raw: str, expected: str) -> None:
    assert normalize_tax_id(raw) == expected


@pytest.mark.parametrize(
    ("tax_id", "kind"),
    [
        ("12345678Z", TaxIdKind.NIF),
        ("X1234567L", TaxIdKind.NIE),
        ("B12345674", TaxIdKind.CIF),  # company: control is a digit
        ("Q1234567D", TaxIdKind.CIF),  # public body: control must be a letter
        ("G1234567D", TaxIdKind.CIF),  # association: digit or letter allowed
        ("G12345674", TaxIdKind.CIF),
    ],
)
def test_valid_tax_ids(tax_id: str, kind: TaxIdKind) -> None:
    result = validate_tax_id(tax_id)

    assert result.is_valid
    assert result.kind == kind


@pytest.mark.parametrize(
    "tax_id",
    [
        "12345678A",  # wrong NIF letter
        "X1234567A",  # wrong NIE letter
        "B12345675",  # wrong CIF control digit
        "B1234567D",  # company CIF must end in a digit
        "Q12345674",  # public body CIF must end in a letter
        "1234567Z",  # too short
        "I1234567D",  # I is not a valid CIF prefix
        "",
        "not a tax id",
    ],
)
def test_invalid_tax_ids(tax_id: str) -> None:
    assert not validate_tax_id(tax_id).is_valid
