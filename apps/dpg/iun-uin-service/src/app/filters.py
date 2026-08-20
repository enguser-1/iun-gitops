"""
MOSIP-compliant UIN filters - ADR-R01 Sénégal (length=10).

Implemented exactly as MOSIP kernel-idvalidator-uin defaults retrieved from
config-server runtime (verified 2026-06-02 on iun-mosip-dev):
  - mosip.kernel.uin.not-start-with: 0,1
  - mosip.kernel.uin.restricted-numbers: 786,666
  - mosip.kernel.uin.length.sequence-limit: 3
  - mosip.kernel.uin.length.repeating-block-limit: 2
  - mosip.kernel.uin.length.repeating-limit: 2
  - mosip.kernel.uin.length.reverse-digits-limit: 5
  - mosip.kernel.uin.length.digits-limit: 5
  - mosip.kernel.uin.length.conjugative-even-digits-limit: 3

The filters protect against trivially-guessable or culturally-sensitive UINs.
"""
from collections import Counter

# Defaults aligned with MOSIP kernel + ADR-R01
NOT_START_WITH = ("0", "1")
RESTRICTED_NUMBERS = ("666", "786")
SEQUENCE_LIMIT = 3
REPEATING_BLOCK_LIMIT = 2
REPEATING_LIMIT = 2
REVERSE_DIGITS_LIMIT = 5
DIGITS_LIMIT = 5
CONJUGATIVE_EVEN_DIGITS_LIMIT = 3

EVEN_DIGITS = {"0", "2", "4", "6", "8"}


def check_not_start_with(uin: str) -> bool:
    """UIN must not start with one of the restricted digits."""
    return bool(uin) and uin[0] not in NOT_START_WITH


def check_restricted(uin: str) -> bool:
    """UIN must not contain any restricted substring."""
    return all(r not in uin for r in RESTRICTED_NUMBERS)


def check_repeating_limit(uin: str, limit: int = REPEATING_LIMIT) -> bool:
    """No same digit repeated more than `limit` times consecutively."""
    if not uin:
        return False
    count = 1
    for i in range(1, len(uin)):
        if uin[i] == uin[i - 1]:
            count += 1
            if count > limit:
                return False
        else:
            count = 1
    return True


def check_sequence_limit(uin: str, limit: int = SEQUENCE_LIMIT) -> bool:
    """No ascending or descending arithmetic sequence longer than `limit`."""
    if not uin:
        return False
    asc = 1
    desc = 1
    for i in range(1, len(uin)):
        diff = int(uin[i]) - int(uin[i - 1])
        if diff == 1:
            asc += 1
            desc = 1
        elif diff == -1:
            desc += 1
            asc = 1
        else:
            asc = 1
            desc = 1
        if asc > limit or desc > limit:
            return False
    return True


def check_repeating_block(uin: str, limit: int = REPEATING_BLOCK_LIMIT) -> bool:
    """No block of `limit`+ digits that immediately repeats (e.g. 1212)."""
    n = len(uin)
    for block_size in range(limit, n // 2 + 1):
        for i in range(n - 2 * block_size + 1):
            if uin[i:i + block_size] == uin[i + block_size:i + 2 * block_size]:
                return False
    return True


def check_reverse_digits(uin: str, limit: int = REVERSE_DIGITS_LIMIT) -> bool:
    """No palindrome substring longer than `limit` digits."""
    n = len(uin)
    if n < limit:
        return True
    for i in range(n - limit + 1):
        substr = uin[i:i + limit]
        if substr == substr[::-1]:
            return False
    return True


def check_digits_limit(uin: str, limit: int = DIGITS_LIMIT) -> bool:
    """No digit appears more than `limit` times in total."""
    counts = Counter(uin)
    return all(c <= limit for c in counts.values())


def check_conjugative_even(uin: str, limit: int = CONJUGATIVE_EVEN_DIGITS_LIMIT) -> bool:
    """No run of `limit`+ consecutive even digits."""
    if not uin:
        return False
    run = 0
    for ch in uin:
        if ch in EVEN_DIGITS:
            run += 1
            if run > limit:
                return False
        else:
            run = 0
    return True


def passes_all_filters(uin: str) -> bool:
    """Apply all filters in order. Short-circuits on first failure."""
    return (
        check_not_start_with(uin)
        and check_restricted(uin)
        and check_repeating_limit(uin)
        and check_sequence_limit(uin)
        and check_repeating_block(uin)
        and check_reverse_digits(uin)
        and check_digits_limit(uin)
        and check_conjugative_even(uin)
    )


def explain_filters(uin: str) -> dict:
    """For debugging : returns a dict of each filter -> pass/fail."""
    return {
        "not_start_with": check_not_start_with(uin),
        "not_restricted": check_restricted(uin),
        "repeating_limit": check_repeating_limit(uin),
        "sequence_limit": check_sequence_limit(uin),
        "repeating_block": check_repeating_block(uin),
        "reverse_digits": check_reverse_digits(uin),
        "digits_limit": check_digits_limit(uin),
        "conjugative_even": check_conjugative_even(uin),
    }
