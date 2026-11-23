"""Named-pattern field extraction and NRIC masking for browser-facing text."""
import re

PATTERNS = {
    'nric': re.compile(r'\b[STFGM]\d{7}[A-Z]\b'),
    'uen': re.compile(r'\b(?:\d{8}[A-Z]|\d{9}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z])\b'),
    'amount': re.compile(r'(?:SGD|S\$|USD|\$)\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?'),
    'date': re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b|\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}\b'),
    'email': re.compile(r'\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b'),
    'phone_sg': re.compile(r'\+65[ -]?\d{4}[ -]?\d{4}'),
    'paragraph_ref': re.compile(r'^\s*(\d{1,2}\.\d{1,2})\s', re.M),
}


def mask_nric(value):
    return value[:3] + '****' + value[-2:]


def mask_text(text):
    return PATTERNS['nric'].sub(lambda m: mask_nric(m.group(0)), text)


def mask_deep(value):
    """Mask NRICs in every string of a JSON-like value (response-only; stored data is untouched)."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, list):
        return [mask_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: mask_deep(v) for k, v in value.items()}
    return value


def extract_fields(text, spans):
    found = []
    for name, pattern in PATTERNS.items():
        for m in pattern.finditer(text):
            g = 1 if name == 'paragraph_ref' else 0
            value = mask_nric(m.group(g)) if name == 'nric' else m.group(g)
            label = next((s['label'] for s in spans if s['start'] <= m.start(g) < s['end']), '')
            found.append({'type': name, 'value': value, 'start': m.start(g), 'end': m.end(g), 'label': label})
    return sorted(found, key=lambda f: f['start'])[:500]  # stable sort keeps pattern order on ties
