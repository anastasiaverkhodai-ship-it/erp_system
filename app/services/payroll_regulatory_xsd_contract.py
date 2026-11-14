"""Pinned electronic form structure, separate from statutory approval.

The schemas initially recovered from EDZV mirrors were byte-verified against
the official DPS archive on 2026-11-13. Passing
XSD validation proves structure only, not legal applicability, source
allocation, classifier validity, arithmetic or permission to file.
"""
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET


class FormSchemaError(ValueError):
    pass


ROOT = Path(__file__).resolve().parents[1] / "data/payroll_forms/2026_11"
FORMS = {"d1": "J0510111", "4df": "J0510411", "d5": "J0510511", "d6": "J0510611"}
HASHES = {
    "J0500111": "df02ee1e3d1a696036e3008322073d1ae43119596edd5105d48fc54d6c56d846",
    "J0510111": "567d002432ea80527f62c488861a7bfc6901fbd177e01f080fb0b83bbd3f3aec",
    "J0510411": "fd4c7305bfd1fe1e5f5848f84be17a9bbb93d677c2f287b44133f30b28adcf27",
    "J0510511": "8e624643d369d75d44d7b64c95a0771a0e2cd40be0c3d36e4744937bf7e18400",
    "J0510611": "a37449eda57ca8a69d5740f521e820b7e652e7268323c321f61fb0595fab48f3",
    "common_types": "f53226747bda74c793f2fd9e9b55d55e5073219f866b2cd29c10e60ba340fef1",
}
XS = "{http://www.w3.org/2001/XMLSchema}"


def _sources(kind):
    if kind not in FORMS:
        raise FormSchemaError("Unsupported payroll form")
    result = {}
    for name in (FORMS[kind], "common_types"):
        try:
            raw = (ROOT / (name + ".xsd")).read_bytes()
        except OSError as exc:
            raise FormSchemaError("Pinned schema unavailable") from exc
        if sha256(raw).hexdigest() != HASHES[name]:
            raise FormSchemaError("Pinned schema digest mismatch")
        result[name] = raw
    return result


def form_structure(kind):
    """Keep the full particle tree: choice is NOT unconditional requiredness.

    In particular minOccurs=0 on a table cell does not make its business
    value optional under the statutory filling procedure.
    """
    raw = _sources(kind)[FORMS[kind]]
    tree = ET.fromstring(raw.decode("windows-1251"))

    def particle(node):
        return {
            "particle": node.tag.removeprefix(XS),
            "attributes": dict(node.attrib),
            "children": [particle(child) for child in node
                         if child.tag != XS + "annotation"],
        }

    return {
        "form_code": FORMS[kind],
        "schema_sha256": HASHES[FORMS[kind]],
        "common_types_sha256": HASHES["common_types"],
        "header": particle(tree.find(f"{XS}complexType[@name='DHead']")),
        "body": particle(tree.find(f"{XS}complexType[@name='DBody']")),
        "official_generation": "BLOCKED",
        "statutory_mapping_approved": False,
    }


def validate_electronic_structure(kind, document: bytes):
    """Validate with libxml2, offline and without exposing taxpayer data.

    Copies verified schemas into a private temporary directory so the
    validator consumes the same bytes whose fingerprints were checked.
    No network, DTD, entities, XInclude or caller-provided schema is used.
    """
    sources = _sources(kind)
    if not isinstance(document, bytes) or not 0 < len(document) <= 4_000_000:
        raise FormSchemaError("XML bytes required (maximum 4 MB)")
    # Restrict encodings to avoid alternate encodings hiding a DTD token.
    if b"\x00" in document or b"<!DOCTYPE" in document or b"<!ENTITY" in document:
        raise FormSchemaError("DTD, entities and wide-character XML prohibited")
    try:
        root = ET.fromstring(document)
    except (ET.ParseError, ValueError) as exc:
        raise FormSchemaError("Invalid XML document") from exc
    if root.tag != "DECLAR":
        raise FormSchemaError("DECLAR root required")
    if any(node.tag.startswith("{http://www.w3.org/2001/XInclude}") for node in root.iter()):
        raise FormSchemaError("XInclude prohibited")
    executable = shutil.which("xmllint")
    if executable is None:
        raise FormSchemaError("XML Schema validator unavailable")
    with tempfile.TemporaryDirectory(prefix="erp-payroll-xsd-") as directory:
        folder = Path(directory)
        for name, raw in sources.items():
            (folder / (name + ".xsd")).write_bytes(raw)
        try:
            result = subprocess.run(
                [executable, "--nonet", "--noout", "--schema",
                 str(folder / (FORMS[kind] + ".xsd")), "-"],
                input=document, capture_output=True, timeout=15,
                cwd=folder,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FormSchemaError("XML Schema validation unavailable") from exc
    if result.returncode != 0:
        # libxml2 diagnostics can contain actual employee identities.
        raise FormSchemaError("Document does not satisfy the pinned form schema")
    return {
        "form_code": FORMS[kind],
        "document_sha256": sha256(document).hexdigest(),
        "structure_valid": True,
        "statutory_mapping_approved": False,
        "official_generation": "BLOCKED",
    }
