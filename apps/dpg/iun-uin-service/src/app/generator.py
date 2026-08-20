"""
UIN generator - combines secure random + Verhoeff + filters.
"""
import secrets
from .verhoeff import compute_check_digit
from .filters import passes_all_filters

UIN_TOTAL_LENGTH = 10  # 9 random digits + 1 Verhoeff check
UIN_BASE_LENGTH = 9


def generate_candidate() -> str:
    """
    Generate a candidate UIN :
      - 9 cryptographically-random digits, first digit in 2-9 (no 0/1 start)
      - 1 Verhoeff check digit appended
    """
    # First digit constrained to 2-9 inclusive (8 options)
    first = secrets.randbelow(8) + 2
    rest = "".join(str(secrets.randbelow(10)) for _ in range(UIN_BASE_LENGTH - 1))
    base = str(first) + rest
    check = compute_check_digit(base)
    return base + str(check)


def generate_uin(max_attempts: int = 200) -> tuple[str, int]:
    """
    Generate a UIN that passes all MOSIP-spec filters.

    Returns:
        (uin, attempts) tuple
    Raises:
        RuntimeError if no valid UIN after `max_attempts` (extremely unlikely
        with default filters - empirical pass rate ~70% per attempt)
    """
    for attempt in range(1, max_attempts + 1):
        candidate = generate_candidate()
        if passes_all_filters(candidate):
            return candidate, attempt
    raise RuntimeError(
        f"Could not generate filter-compliant UIN in {max_attempts} attempts"
    )
