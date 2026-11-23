from defusedxml.common import DTDForbidden
import pytest

from semantic_pipeline_v2.xfa import field_lines


def test_xfa_recurring_fields_and_schema_exclusion():
    packet = b"""<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/">
      <xfa:data><form><account><currency>SGD</currency></account>
      <account><currency>USD</currency><note>first&#10;second</note><empty/></account></form></xfa:data>
      <schema><currency>EXAMPLE</currency></schema></xfa:datasets>"""
    assert field_lines(packet) == [
        'xfa:data/form[1]/account[1]/currency[1] = "SGD"',
        'xfa:data/form[1]/account[2]/currency[1] = "USD"',
        'xfa:data/form[1]/account[2]/note[1] = "first\\nsecond"',
    ]


def test_xfa_rejects_missing_values_and_external_declarations():
    with pytest.raises(ValueError, match="no populated"):
        field_lines(b'<datasets/>')
    with pytest.raises(DTDForbidden):
        field_lines(b'<!DOCTYPE data><data/>')
