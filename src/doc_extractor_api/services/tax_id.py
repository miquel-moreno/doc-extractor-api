"""Spanish tax ID validation (NIF, NIE and CIF) with check-character verification."""

import re
from dataclasses import dataclass
from enum import StrEnum

NIF_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
CIF_CONTROL_LETTERS = "JABCDEFGHI"

# CIF prefixes by the kind of control character they allow.
CIF_LETTER_CONTROL = set("KNPQRSW")
CIF_DIGIT_CONTROL = set("ABEH")
CIF_PREFIXES = set("ABCDEFGHJKLMNPQRSUVW")

_NIF_RE = re.compile(r"^(\d{8})([A-Z])$")
_NIE_RE = re.compile(r"^([XYZ])(\d{7})([A-Z])$")
_CIF_RE = re.compile(r"^([A-Z])(\d{7})([0-9A-J])$")


class TaxIdKind(StrEnum):
    NIF = "nif"
    NIE = "nie"
    CIF = "cif"


@dataclass(frozen=True)
class TaxIdResult:
    normalized: str
    kind: TaxIdKind | None
    is_valid: bool


def normalize_tax_id(raw: str) -> str:
    """Uppercase, drop spaces/dots/dashes and an optional 'ES' VAT prefix."""
    value = re.sub(r"[\s.\-]", "", raw).upper()
    if value.startswith("ES") and len(value) == 11:
        value = value[2:]
    return value


def validate_tax_id(raw: str) -> TaxIdResult:
    value = normalize_tax_id(raw)

    if match := _NIF_RE.match(value):
        number, letter = match.groups()
        return TaxIdResult(value, TaxIdKind.NIF, NIF_LETTERS[int(number) % 23] == letter)

    if match := _NIE_RE.match(value):
        prefix, digits, letter = match.groups()
        number = int(str("XYZ".index(prefix)) + digits)
        return TaxIdResult(value, TaxIdKind.NIE, NIF_LETTERS[number % 23] == letter)

    if (match := _CIF_RE.match(value)) and match.group(1) in CIF_PREFIXES:
        prefix, digits, control = match.groups()
        return TaxIdResult(value, TaxIdKind.CIF, _is_valid_cif_control(prefix, digits, control))

    return TaxIdResult(value, None, False)


def _is_valid_cif_control(prefix: str, digits: str, control: str) -> bool:
    even_sum = sum(int(d) for d in digits[1::2])
    odd_sum = sum(sum(divmod(int(d) * 2, 10)) for d in digits[::2])
    control_digit = (10 - (even_sum + odd_sum) % 10) % 10
    expected_letter = CIF_CONTROL_LETTERS[control_digit]

    if prefix in CIF_LETTER_CONTROL:
        return control == expected_letter
    if prefix in CIF_DIGIT_CONTROL:
        return control == str(control_digit)
    return control in (str(control_digit), expected_letter)
