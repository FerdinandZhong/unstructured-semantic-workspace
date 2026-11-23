"""Read populated data from an already verified, allowlisted XFA source."""

from collections import Counter
from io import BytesIO
import json

from defusedxml.ElementTree import fromstring
from pypdf import PdfReader


XFA_DATA = "{http://www.xfa.org/schema/xfa-data/1.0/}"


def field_lines(packet):
    root = fromstring(packet, forbid_dtd=True)
    data = root.find(XFA_DATA + "data")
    if root.tag != XFA_DATA + "datasets" or data is None:
        raise ValueError("The PDF has no populated XFA data packet.")

    def walk(parent, prefix):
        positions = Counter()
        for child in parent:
            name = child.tag.rsplit("}", 1)[-1]
            positions[name] += 1
            path = f"{prefix}/{name}[{positions[name]}]"
            if len(child):
                yield from walk(child, path)
            elif child.text and child.text.strip():
                # A field always occupies one line even when its value contains line breaks.
                yield f"{path} = {json.dumps(child.text, ensure_ascii=False)}"

    lines = list(walk(data, "xfa:data"))
    if not lines:
        raise ValueError("The XFA form has no populated fields.")
    return lines


def form_pages(raw: bytes) -> list[dict]:
    """Return one logical field listing. Its index has no physical page meaning."""
    reader = PdfReader(BytesIO(raw), strict=False)
    form = reader.trailer["/Root"].get("/AcroForm")
    packets = form.get_object().get("/XFA") if form else None
    packets = packets.get_object() if packets is not None else None
    if isinstance(packets, list) and len(packets) % 2 == 0:
        datasets = [stream for name, stream in zip(packets[::2], packets[1::2]) if name == "datasets"]
        if len(datasets) == 1:
            lines = field_lines(datasets[0].get_object().get_data())
            return [{"index": 0, "markdown": "\n".join(lines)}]
    raise ValueError("The PDF has no supported XFA datasets packet.")
