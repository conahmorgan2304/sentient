"""Numbers said as words, on their way to the voice.

A text-to-speech voice in many languages reads "4,4", "14:30", "40%" or "5–10" badly: digit by digit, or saying the
comma, the colon and the dash. Before speaking, Sentient writes them out ("cztery przecinek cztery", "czternaście
trzydzieści", "czterdzieści procent", "pięć do dziesięciu" is beyond us, so "pięć do dziesięć").

Words come from ``num2words``. Grammar after a number (Polish cases, say) isn't attempted here: the AI is asked to write
numbers as words itself (llm_api), and this catches the rest, including Sentient's own sentences.

English is left alone (English voices read numbers well), and so are Chinese, Japanese and Korean (their voices read
digits natively) and any language ``num2words`` doesn't know. Switch: "Say numbers as words" (on by default).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import logging
import re

_LOGGER = logging.getLogger(__name__)

CONF_SAY_NUMBERS = "say_numbers_as_words"

# Home Assistant's code (lower case) → num2words's, where they differ
_CODES = {"pt-br": "pt_BR", "nb": "no", "nn": "no", "no": "no", "zh-hans": None, "zh-hant": None}
_SKIP = {"en", "zh", "ja", "ko"}

# Languages that write 1.234,5 (a comma for decimals); the rest write 1,234.5
_DECIMAL_POINT = {"he", "th", "hi", "bn", "ar"}

_PERCENT = {"pl": "procent", "de": "Prozent", "nl": "procent", "it": "per cento", "pt": "por cento", "pt_BR": "por cento",
            "es": "por ciento", "fr": "pour cent", "hu": "százalék", "sv": "procent", "da": "procent", "no": "prosent",
            "fi": "prosenttia", "cs": "procent", "ro": "la sută", "tr": "yüzde", "ru": "процентов", "uk": "відсотків",
            "ar": "بالمئة", "he": "אחוז", "bn": "শতাংশ", "id": "persen", "vi": "phần trăm", "th": "เปอร์เซ็นต์",
            "sk": "percent", "sl": "odstotkov", "hr": "posto", "bg": "процента", "lt": "procentų", "lv": "procenti",
            "et": "protsenti", "ca": "per cent", "cy": "y cant", "ga": "faoin gcéad"}
_DEGREES = {"pl": "stopni", "de": "Grad", "nl": "graden", "it": "gradi", "pt": "graus", "pt_BR": "graus", "es": "grados",
            "fr": "degrés", "hu": "fok", "sv": "grader", "da": "grader", "no": "grader", "fi": "astetta", "cs": "stupňů",
            "ro": "grade", "tr": "derece", "ru": "градусов", "uk": "градусів", "ar": "درجة", "he": "מעלות",
            "bn": "ডিগ্রি", "id": "derajat", "vi": "độ", "th": "องศา", "sk": "stupňov", "sl": "stopinj", "hr": "stupnjeva",
            "bg": "градуса", "lt": "laipsnių", "lv": "grādi", "et": "kraadi", "ca": "graus", "cy": "gradd", "ga": "céim"}
_TO = {"pl": "do", "de": "bis", "nl": "tot", "it": "a", "pt": "a", "pt_BR": "a", "es": "a", "fr": "à", "hu": "–",
       "sv": "till", "da": "til", "no": "til", "fi": "–", "cs": "až", "ro": "până la", "tr": "ile", "ru": "до",
       "uk": "до", "ar": "إلى", "he": "עד", "bn": "থেকে", "id": "sampai", "vi": "đến", "th": "ถึง", "sk": "až",
       "sl": "do", "hr": "do", "bg": "до", "lt": "iki", "lv": "līdz", "et": "kuni", "ca": "a", "cy": "i", "ga": "go"}
_COMMA = {"tr": "virgül"}
_MINUS = {"ru": "минус", "uk": "мінус", "bg": "минус", "fr": "moins", "es": "menos", "it": "meno", "pt": "menos",
          "pt_BR": "menos", "tr": "eksi", "fi": "miinus", "hu": "mínusz", "ar": "سالب", "he": "מינוס", "th": "ลบ",
          "vi": "âm", "bn": "ঋণাত্মক", "ca": "menys", "lt": "minus", "et": "miinus"}
# where the word after a number changes with it: one, two to four, five and up (and a fraction takes the second)
_FORMS = {
    "pl": {"%": ("procent", "procent", "procent", "procent"), "°": ("stopień", "stopnie", "stopni", "stopnia")},
    "cs": {"%": ("procento", "procenta", "procent", "procenta"), "°": ("stupeň", "stupně", "stupňů", "stupně")},
    "ru": {"%": ("процент", "процента", "процентов", "процента"), "°": ("градус", "градуса", "градусов", "градуса")},
    "uk": {"%": ("відсоток", "відсотки", "відсотків", "відсотка"), "°": ("градус", "градуси", "градусів", "градуса")},
}


def _unit(value: Decimal, code: str, unit: str) -> str:
    """The word for % or ° after *value*."""
    if forms := _FORMS.get(code, {}).get(unit):
        if value != value.to_integral_value():
            return forms[3]
        n = abs(int(value))
        if code != "pl" and n % 10 == 1 and n % 100 != 11:
            return forms[0]
        if n == 1:
            return forms[0]
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return forms[1]
        return forms[2]
    return (_PERCENT if unit == "%" else _DEGREES).get(code, unit)  # num2words's own decimals are wrong here; said as whole, word, decimals

_NUM = r"\d+(?:[  .,]\d{3})*(?:[.,]\d+)?"
# not part of a word, a code or an id ("v1.2", "mp3", "sensor_2", "WD-40", "2nd")
_EDGE_L = r"(?<![\w.,:/#°%-])"
_EDGE_R = r"(?![\w:/#]|[.,]\d|-\d)"
_TIME = re.compile(_EDGE_L + r"([01]?\d|2[0-3]):([0-5]\d)" + _EDGE_R)
_RANGE = re.compile(_EDGE_L + rf"({_NUM})\s?[-–]\s?({_NUM})" + _EDGE_R)
# a minus only at the start of a word ("-3", not "WD-40")
_ONE = re.compile(r"(?:(?<![^\s(\[])(-)|(?<![\w.,:/#°%-]))" + rf"({_NUM})" + r"(\s?%|\s?°C?)?" + _EDGE_R)


def converter(language: str | None) -> str | None:
    """The num2words language for *language*, or None when numbers are left as digits."""
    if not language:
        return None
    low = str(language).lower().replace("_", "-")
    if low.split("-")[0] in _SKIP:
        return None
    code = _CODES.get(low, low.split("-")[0])
    if code is None:
        return None
    try:
        from num2words import CONVERTER_CLASSES
    except ImportError:  # the requirement wasn't installed: speak digits rather than fail
        return None
    return code if code in CONVERTER_CLASSES else None


def _value(raw: str, code: str) -> Decimal | None:
    point = "." if code in _DECIMAL_POINT else ","
    group = {" ", " ", "," if point == "." else "."}
    text = raw
    # "1.234" in a decimal-comma language (and "1,234" in a decimal-point one) is a thousand, not a fraction
    for g in group:
        text = re.sub(rf"(?<=\d){re.escape(g)}(?=\d{{3}}(?:\D|$))", "", text)
    text = text.replace(point, ".").replace(",", ".")
    if text.count(".") > 1:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _say(value: Decimal, code: str) -> str:
    from num2words import num2words

    if value < 0:
        return _MINUS.get(code, "minus") + " " + _say(-value, code)
    if value == value.to_integral_value():
        return num2words(int(value), lang=code)
    if code in _COMMA:
        whole, _, frac = format(abs(value), "f").partition(".")
        lead = "".join(num2words(0, lang=code) + " " for c in frac[:-1] if c == "0" and frac.startswith("0"))
        out = f"{num2words(int(whole), lang=code)} {_COMMA[code]} {lead}{num2words(int(frac), lang=code)}"
        return out
    return num2words(value, lang=code)


def words(text: str, language: str | None) -> str:
    """*text* with its numbers, times, percentages and ranges written as words in *language*."""
    if not text or not any(c.isdigit() for c in text) or (code := converter(language)) is None:
        return text
    try:
        def time(m: re.Match) -> str:
            hour, minute = int(m[1]), int(m[2])
            if not minute:
                return _say(Decimal(hour), code)
            zero = _say(Decimal(0), code) + " " if minute < 10 else ""
            return f"{_say(Decimal(hour), code)} {zero}{_say(Decimal(minute), code)}"

        def span(m: re.Match) -> str:
            a, b = _value(m[1], code), _value(m[2], code)
            if a is None or b is None or b < a:
                return m[0]  # not a range ("2026-11-25", a phone number)
            return f"{_say(a, code)} {_TO.get(code, '–')} {_say(b, code)}"

        def one(m: re.Match) -> str:
            value = _value(m[2], code)
            if value is None:
                return m[0]
            if m[1]:
                value = -value
            said = _say(value, code)
            if unit := (m[3] or "").strip()[:1]:
                said += " " + _unit(value, code, unit)
            return said

        text = _TIME.sub(time, text)
        text = _RANGE.sub(span, text)
        return _ONE.sub(one, text)
    except Exception:  # noqa: BLE001 - a number read as digits is better than no reply
        _LOGGER.debug("Couldn't write the numbers in %r as words", text, exc_info=True)
        return text
