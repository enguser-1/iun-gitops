"""
VID generator - identifiant virtuel de 16 chiffres, conforme aux regles MOSIP.

Le VID etait jusqu'ici genere DANS le bridge (`gen_vid_local`), sans que le
registre souverain n'en sache rien : il n'etait donc ni revocable ni rotatif.
Il est desormais emis et detenu ici, par le generateur central.

Regles reprises de MOSIP kernel-idvalidator-vid :
  - longueur 16, dernier chiffre = cle de Verhoeff
  - premier chiffre dans 2-9
  - pas plus de 2 chiffres identiques consecutifs
  - pas de sequence croissante ou decroissante de 3 chiffres ou plus
"""
import secrets

from .verhoeff import compute_check_digit

VID_TOTAL_LENGTH = 16
VID_BASE_LENGTH = 15

SEQUENCE_LIMIT = 3
REPEATING_LIMIT = 2


def _has_repeating(vid: str, limit: int = REPEATING_LIMIT) -> bool:
    run = 1
    for i in range(1, len(vid)):
        run = run + 1 if vid[i] == vid[i - 1] else 1
        if run > limit:
            return True
    return False


def _has_sequence(vid: str, limit: int = SEQUENCE_LIMIT) -> bool:
    asc = desc = 1
    for i in range(1, len(vid)):
        delta = int(vid[i]) - int(vid[i - 1])
        asc = asc + 1 if delta == 1 else 1
        desc = desc + 1 if delta == -1 else 1
        if asc >= limit or desc >= limit:
            return True
    return False


def passes_all_filters(vid: str) -> bool:
    if len(vid) != VID_TOTAL_LENGTH or not vid.isdigit():
        return False
    if vid[0] in ("0", "1"):
        return False
    if _has_repeating(vid):
        return False
    if _has_sequence(vid):
        return False
    return True


def generate_candidate() -> str:
    first = secrets.randbelow(8) + 2
    rest = "".join(str(secrets.randbelow(10)) for _ in range(VID_BASE_LENGTH - 1))
    base = str(first) + rest
    return base + str(compute_check_digit(base))


def generate_vid(max_attempts: int = 200):
    """Retourne (vid, tentatives). Leve RuntimeError si aucun candidat valide."""
    for attempt in range(1, max_attempts + 1):
        candidate = generate_candidate()
        if passes_all_filters(candidate):
            return candidate, attempt
    raise RuntimeError(
        "Aucun VID conforme apres %d tentatives" % max_attempts
    )


def format_vid(vid: str) -> str:
    """Presentation en quatre groupes de quatre - jamais stockee sous cette forme."""
    v = "".join(ch for ch in str(vid) if ch.isdigit())
    if len(v) != VID_TOTAL_LENGTH:
        return str(vid)
    return "-".join(v[i:i + 4] for i in range(0, len(v), 4))
