"""
Verhoeff check-digit algorithm.

Reference implementation aligned with MOSIP kernel-idvalidator-uin (Java).
Verhoeff is a Dihedral D5 group based algorithm that detects all single-digit
errors plus most transposition errors - which is why MOSIP standardized on it
for the UIN check digit.

Authoritative tables : the standard D5 multiplication, P permutation,
and INV inverse tables.
"""

# Dihedral D5 multiplication table
D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

# Permutation table - 8 rows applied cyclically based on position
P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

# Inverse table
INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def compute_check_digit(number: str) -> int:
    """
    Compute the Verhoeff check digit for a digit string.

    Args:
        number: digit string without check digit (e.g. 9 digits for a 10-digit UIN)

    Returns:
        The check digit (0-9)

    Raises:
        ValueError if input is not a non-empty digit string

    Algorithm aligned with MOSIP kernel-idvalidator-uin Java reference :
        c = 0
        for i in 0..len-1:
            c = D[c][P[(i+1) % 8][digit_at_position(len-1-i)]]
        check = INV[c]
    """
    if not number or not number.isdigit():
        raise ValueError("input must be a non-empty digit string")
    c = 0
    length = len(number)
    for i in range(length):
        digit = int(number[length - 1 - i])
        c = D[c][P[(i + 1) % 8][digit]]
    return INV[c]


def validate(uin_with_check: str) -> bool:
    """
    Validate a Verhoeff-checked number (full number including check digit).

    Args:
        uin_with_check: digit string with the check digit appended

    Returns:
        True if check digit is valid, False otherwise.

    Algorithm aligned with MOSIP kernel-idvalidator-uin Java reference :
        c = 0
        for i in 0..len-1:
            c = D[c][P[i % 8][digit_at_position(len-1-i)]]
        return c == 0
    """
    if not uin_with_check or not uin_with_check.isdigit():
        return False
    c = 0
    length = len(uin_with_check)
    for i in range(length):
        digit = int(uin_with_check[length - 1 - i])
        c = D[c][P[i % 8][digit]]
    return c == 0
