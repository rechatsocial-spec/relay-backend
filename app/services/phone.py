"""Turns whatever people type or paste into an E.164 number WhatsApp accepts."""
import re


def normalize_phone(raw: str, default_country_code: str = "91") -> tuple[str | None, str | None]:
    """
    Returns (e164, None) or (None, reason).
      "98200 11234"        -> +919820011234   (10 digits: default country code added)
      "098200 11234"       -> +919820011234   (leading trunk 0 dropped)
      "919820011234"       -> +919820011234
      "+1 (415) 523-8886"  -> +14155238886
      "0044 7700 900123"   -> +447700900123   (00 international prefix)
    """
    s = (raw or "").strip()
    if not s:
        return None, "empty"
    international = s.startswith("+") or s.startswith("00")
    digits = re.sub(r"\D", "", s)
    if s.startswith("00"):
        digits = digits[2:]
    if not digits:
        return None, "no digits"

    cc = re.sub(r"\D", "", default_country_code or "")
    if not international and cc:
        if len(digits) == 10:
            digits = cc + digits
        elif len(digits) == 11 and digits.startswith("0"):
            digits = cc + digits[1:]

    if not 8 <= len(digits) <= 15:
        return None, "wrong length"
    if digits.startswith("91") and len(digits) == 12 and digits[2] not in "6789":
        return None, "not an Indian mobile number"
    return f"+{digits}", None
