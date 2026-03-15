"""PDF digital-signature support (PKCS#11 smart cards and PKCS#12 files)."""

import os
import sys
import glob

# pyHanko is an optional dependency ─ import lazily so the app can still
# open/edit PDFs when pyHanko is missing.
try:
    from pyhanko.sign import signers, fields
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    PYHANKO_AVAILABLE = True
except ImportError:
    PYHANKO_AVAILABLE = False

try:
    from pyhanko.sign.pkcs11 import PKCS11Signer  # needs python-pkcs11

    PKCS11_AVAILABLE = True
except ImportError:
    PKCS11_AVAILABLE = False

_IS_WINDOWS = sys.platform == "win32"

# Usual PKCS#11 library locations per platform
_PKCS11_SEARCH_LINUX = [
    "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so",
    "/usr/lib/opensc-pkcs11.so",
    "/usr/lib64/opensc-pkcs11.so",
    "/usr/lib/x86_64-linux-gnu/pkcs11/opensc-pkcs11.so",
    "/usr/lib/aarch64-linux-gnu/opensc-pkcs11.so",
]

_PKCS11_SEARCH_WINDOWS = [
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                 "OpenSC Project", "OpenSC", "pkcs11", "opensc-pkcs11.dll"),
    os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 "OpenSC Project", "OpenSC", "pkcs11", "opensc-pkcs11.dll"),
    r"C:\Windows\System32\opensc-pkcs11.dll",
]


def find_pkcs11_library():
    """Return the first PKCS#11 library found on this system, or *None*."""
    candidates = _PKCS11_SEARCH_WINDOWS if _IS_WINDOWS else _PKCS11_SEARCH_LINUX
    for p in candidates:
        if os.path.isfile(p):
            return p
    if not _IS_WINDOWS:
        hits = glob.glob("/usr/lib/**/opensc-pkcs11.so", recursive=True)
        return hits[0] if hits else None
    return None


# ── signer factories ────────────────────────────────────────────


def create_pkcs11_signer(pkcs11_lib, slot_no=0, pin=None, key_label=None, cert_label=None):
    """Create a pyHanko signer backed by a PKCS#11 smart-card token."""
    if not PKCS11_AVAILABLE:
        raise RuntimeError(
            "PKCS#11 support is not installed.\n"
            "Install with:  pip install 'pyHanko[pkcs11]'"
        )
    kwargs = dict(
        pkcs11_lib_path=pkcs11_lib,
        slot_no=slot_no,
        user_pin=pin,
    )
    if key_label:
        kwargs["key_label"] = key_label
    if cert_label:
        kwargs["cert_label"] = cert_label
    return PKCS11Signer(**kwargs)


def create_pkcs12_signer(pfx_path, passphrase=None):
    """Create a pyHanko signer from a .p12 / .pfx certificate file."""
    if not PYHANKO_AVAILABLE:
        raise RuntimeError(
            "pyHanko is not installed.\n"
            "Install with:  pip install 'pyHanko[pkcs11]'"
        )
    pw = passphrase.encode("utf-8") if passphrase else None
    return signers.SimpleSigner.load_pkcs12(pfx_file=pfx_path, passphrase=pw)


# ── signing ─────────────────────────────────────────────────────


def sign_pdf(
    input_path,
    output_path,
    signer,
    field_name="Signature",
    reason=None,
    location=None,
    visible=False,
    page=0,
    rect=None,
):
    """
    Sign *input_path* and write the result to *output_path*.

    *signer* must be a pyHanko ``Signer`` instance (from one of the
    ``create_*`` helpers above).
    """
    if not PYHANKO_AVAILABLE:
        raise RuntimeError("pyHanko is not installed.")

    with open(input_path, "rb") as fh:
        writer = IncrementalPdfFileWriter(fh)

        if visible and rect:
            fields.append_signature_field(
                writer,
                fields.SigFieldSpec(
                    sig_field_name=field_name,
                    on_page=page,
                    box=rect,
                ),
            )

        meta = signers.PdfSignatureMetadata(
            field_name=field_name,
            reason=reason,
            location=location,
        )

        pdf_signer = signers.PdfSigner(
            signature_meta=meta,
            signer=signer,
        )

        with open(output_path, "wb") as out:
            pdf_signer.sign_pdf(writer, output=out)
