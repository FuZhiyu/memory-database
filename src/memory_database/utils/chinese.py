from typing import Tuple

def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    # Basic CJK Unified Ideographs
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
        or 0xF900 <= code <= 0xFAFF
        or 0x2F800 <= code <= 0x2FA1F
    )


def contains_chinese(text: str) -> bool:
    return any(_is_cjk(c) for c in text or "")


def split_chinese_name(name: str) -> Tuple[str, str]:
    """
    Heuristically split a Chinese name into (family, given).

    Rules:
    - If whitespace present and both parts contain Chinese: assume format "given family"
    - Else if contiguous Chinese chars length >= 2: family = first char, given = rest
    - Else return ("", "")
    """
    if not name:
        return "", ""
    name = name.strip()
    # whitespace split
    parts = [p for p in name.split() if p]
    if len(parts) == 2 and all(contains_chinese(p) for p in parts):
        given, family = parts[0], parts[1]
        return family, given
    # contiguous Chinese fallback
    only_cjk = "".join(c for c in name if _is_cjk(c))
    if len(only_cjk) >= 2:
        family = only_cjk[0]
        given = only_cjk[1:]
        return family, given
    return "", ""


def chinese_aliases(name: str) -> Tuple[str, str]:
    """
    Given a Chinese name, produce:
    - Chinese order alias: family+given (no space)
    - English pinyin alias: Given Family (capitalized, no hyphens)
    """
    from pypinyin import lazy_pinyin

    fam, giv = split_chinese_name(name)
    if not fam or not giv:
        return "", ""

    chinese_form = f"{fam}{giv}"

    # Build pinyin strings and format as "Tianxing Zheng"
    def cap_word(chars: str) -> str:
        parts = lazy_pinyin(chars)
        if not parts:
            return ""
        joined = "".join(parts)
        return joined.capitalize()

    eng = f"{cap_word(giv)} {cap_word(fam)}"
    return chinese_form, eng
