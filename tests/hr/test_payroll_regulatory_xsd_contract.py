import shutil
import xml.etree.ElementTree as ET

import pytest

from app.services import payroll_regulatory_xsd_contract as contract


def sample(kind):
    """Structural fixture only; not a legally complete declaration."""
    root = ET.Element("DECLAR")
    head = ET.SubElement(root, "DECLARHEAD")
    values = {
        "TIN": "12345678", "C_DOC": "J05",
        "C_DOC_SUB": contract.FORMS[kind][3:6], "C_DOC_VER": "11",
        "C_DOC_TYPE": "0", "C_DOC_CNT": "1", "C_REG": "26", "C_RAJ": "1",
        "PERIOD_MONTH": "10", "PERIOD_TYPE": "1", "PERIOD_YEAR": "2026",
        "C_STI_ORIG": "2601", "C_DOC_STAN": "1", "D_FILL": "05112026",
    }
    for key, value in values.items():
        ET.SubElement(head, key).text = value
    body = ET.SubElement(root, "DECLARBODY")
    values = {"HZ": "1", "HZY": "2026", "HZM": "10"}
    if kind == "4df":
        values["HNUM"] = "1"
    values.update(HNAME="Тест", HTIN="12345678")
    if kind == "4df":
        values["HKATOTTG"] = "UA80000000000093317"
    values.update(HFILL="05112026", HKBOS="1234567890", HBOS="Тест")
    for key, value in values.items():
        ET.SubElement(body, key).text = value
    return root


@pytest.mark.parametrize("kind", contract.FORMS)
def test_real_schema_accepts_structural_fixture_but_not_filing_approval(kind):
    result = contract.validate_electronic_structure(kind, ET.tostring(sample(kind)))
    assert result["structure_valid"]
    assert result["official_generation"] == "BLOCKED"
    assert not result["statutory_mapping_approved"]


@pytest.mark.parametrize("kind", contract.FORMS)
def test_schema_enforces_report_type_choice(kind):
    root = sample(kind)
    root.find("DECLARBODY").insert(1, ET.Element("HZU"))
    with pytest.raises(contract.FormSchemaError, match="does not satisfy"):
        contract.validate_electronic_structure(kind, ET.tostring(root))


@pytest.mark.parametrize("kind", contract.FORMS)
def test_schema_rejects_wrong_version_and_missing_signer(kind):
    root = sample(kind)
    root.find("DECLARHEAD/C_DOC_VER").text = "10"
    with pytest.raises(contract.FormSchemaError, match="does not satisfy"):
        contract.validate_electronic_structure(kind, ET.tostring(root))
    root = sample(kind)
    root.find("DECLARBODY").remove(root.find("DECLARBODY/HKBOS"))
    with pytest.raises(contract.FormSchemaError, match="does not satisfy"):
        contract.validate_electronic_structure(kind, ET.tostring(root))


def test_schema_detects_duplicate_cell_row_number():
    root = sample("d1")
    body = root.find("DECLARBODY")
    for _ in range(2):
        cell = ET.Element("T1RXXXXG16", ROWNUM="1")
        cell.text = "100.00"
        body.insert(5, cell)
    with pytest.raises(contract.FormSchemaError, match="does not satisfy"):
        contract.validate_electronic_structure("d1", ET.tostring(root))


def test_schema_structure_preserves_choice_and_optional_row_cells():
    tree = contract.form_structure("d1")["body"]["children"][0]["children"]
    assert tree[0]["particle"] == "choice"
    cell = next(x for x in tree if x["attributes"].get("name") == "T1RXXXXG16")
    assert cell["attributes"]["minOccurs"] == "0"
    assert cell["attributes"]["maxOccurs"] == "999999"


def test_tampered_dependency_rejected(tmp_path, monkeypatch):
    shutil.copytree(contract.ROOT, tmp_path / "schemas")
    monkeypatch.setattr(contract, "ROOT", tmp_path / "schemas")
    (contract.ROOT / "common_types.xsd").write_bytes(b"altered")
    with pytest.raises(contract.FormSchemaError, match="digest mismatch"):
        contract.form_structure("d1")


@pytest.mark.parametrize("raw", [
    b'<!DOCTYPE DECLAR [<!ENTITY secret SYSTEM "file:///etc/passwd">]><DECLAR>&secret;</DECLAR>',
    '<!DOCTYPE DECLAR><DECLAR/>'.encode("utf-16"),
    b'<DECLAR><xi:include xmlns:xi="http://www.w3.org/2001/XInclude" href="file:///etc/passwd"/></DECLAR>',
])
def test_external_resource_requests_rejected(raw):
    with pytest.raises(contract.FormSchemaError, match="prohibited"):
        contract.validate_electronic_structure("d1", raw)


def test_validator_absence_is_not_success(monkeypatch):
    monkeypatch.setattr(contract.shutil, "which", lambda name: None)
    with pytest.raises(contract.FormSchemaError, match="unavailable"):
        contract.validate_electronic_structure("d1", ET.tostring(sample("d1")))


def test_readiness_reports_recovered_bytes_separately_from_approval():
    from datetime import date
    from app.services.payroll_regulatory_official_field_catalogue import field_review_status
    status = field_review_status(period_start=date(2026, 10, 1), period_end=date(2026, 10, 31))
    for kind in contract.FORMS:
        item = status["forms"][kind]
        assert item["electronic_schema_artifact"]["status"] == "OFFICIAL_BYTES_VERIFIED"
        assert item["field_mapping_approved"] == 0
        assert item["official_generation"] == "BLOCKED"
