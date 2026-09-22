"""
Security primitives for TrustCatalog.

PASSWORD HASHING
-----------------
This sandbox has no network access, so bcrypt / argon2-cffi cannot be
installed (pip install fails offline). We use Python's stdlib
`hashlib.pbkdf2_hmac` instead - PBKDF2-HMAC-SHA256 is a standard,
NIST-approved (SP 800-132) key derivation function, not a homemade
algorithm. It is an acceptable prototype choice, but bcrypt/argon2id are
preferred for production because they are deliberately memory-hard.

TO UPGRADE LATER (on a machine with internet access):
    pip install argon2-cffi
    replace hash_password()/verify_password() below with argon2.PasswordHasher
Everything that calls these two functions is isolated to this file, so the
swap touches nothing else.

Stored format:  pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
The iteration count is embedded so it can be raised later without breaking
existing hashes (verify_password reads whatever count is stored).
"""

import hashlib
import hmac
import html
import os
import re
import secrets
import urllib.parse

PBKDF2_ITERATIONS = 260_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Hash a plaintext password. Never call this on anything you plan to log."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification against a hash produced by hash_password()."""
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(candidate, expected)


# ---------------------------------------------------------------------
# Input validation - every user-controlled field goes through one of these
# before it reaches a database write. None of this is a substitute for the
# parameterized-query rule in src/shop_db.py; it rejects bad data early so
# a bad value never gets the chance to reach SQL at all.
# ---------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\.\-]{3,32}$")

MAX_TEXT = 2000
MAX_NAME = 200


class ValidationError(ValueError):
    """Raised by the validators below; callers turn this into a friendly message."""


def clean_text(value, field, max_len=MAX_TEXT, required=True):
    if value is None:
        value = ""
    value = str(value).strip()
    if required and not value:
        raise ValidationError(f"{field} is required.")
    if len(value) > max_len:
        raise ValidationError(f"{field} must be under {max_len} characters.")
    # Strip control characters (defends against log/UI injection tricks).
    value = "".join(ch for ch in value if ch == "\n" or ord(ch) >= 32)
    return value


def clean_email(value):
    value = clean_text(value, "Email", max_len=254)
    if not EMAIL_RE.match(value):
        raise ValidationError("Enter a valid email address.")
    return value.lower()


def clean_username(value):
    value = clean_text(value, "Username", max_len=32)
    if not USERNAME_RE.match(value):
        raise ValidationError(
            "Username must be 3-32 characters: letters, numbers, '.', '_', '-' only."
        )
    return value.lower()


def check_password_strength(password):
    if not password or len(password) < 8:
        raise ValidationError("Password must be at least 8 characters.")
    if len(password) > 256:
        raise ValidationError("Password is too long.")
    if password.lower() in ("password", "password123", "12345678", "qwertyui"):
        raise ValidationError("That password is too common. Choose another.")
    return password


def clean_price(value, field="Price"):
    try:
        price = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number.")
    if price < 0:
        raise ValidationError(f"{field} cannot be negative.")
    if price > 10_000_000:
        raise ValidationError(f"{field} is unreasonably large.")
    return round(price, 2)


def clean_quantity(value, field="Quantity", max_qty=10_000):
    try:
        qty = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a whole number.")
    if qty < 0:
        raise ValidationError(f"{field} cannot be negative.")
    if qty > max_qty:
        raise ValidationError(f"{field} cannot exceed {max_qty}.")
    return qty


def clean_int_id(value, field="ID"):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} is invalid.")


def escape_html(value):
    """Escape text before it is ever interpolated into raw HTML/markdown."""
    return html.escape(str(value), quote=True)


_BLOCKED_URL_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "169.254.169.254",  # cloud metadata endpoint (AWS/GCP/Azure) - never allow
}


def clean_image_url(value):
    """
    Validate a product image URL (the admin/seller "paste a URL" option).

    Deliberately does NOT fetch the URL to check it - the server never
    makes an outbound request to a user-supplied URL at all, which is what
    actually prevents SSRF here (there is no fetch for an attacker to
    redirect at an internal service). The browser loads the image directly
    via <img src="...">/st.image(url), exactly like any ordinary web page.
    This function only checks the URL's *shape*: scheme, a plausible host,
    and rejects obviously internal/local targets as an extra precaution
    even though nothing server-side ever connects to them.
    """
    if value is None or str(value).strip() == "":
        return None
    value = str(value).strip()
    if len(value) > 2000:
        raise ValidationError("Image URL is too long.")

    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("Image URL must start with http:// or https://.")
    if not parsed.netloc:
        raise ValidationError("Image URL is missing a host.")

    host = parsed.hostname or ""
    if host.lower() in _BLOCKED_URL_HOSTS:
        raise ValidationError("That host is not allowed for image URLs.")
    if host.lower().endswith(".local") or host.startswith("192.168.") or host.startswith("10."):
        raise ValidationError("Local/private network addresses are not allowed for image URLs.")

    return value


def clean_image_filename(value):
    """
    Validate a value being stored in catalog_products.image_filename. Only
    a bare filename with an allowed extension may be stored - never a path
    or URL. Returns None for an empty value (meaning "no image"). This is
    the write-time counterpart to src.images.resolve_product_image, which
    re-derives safety from the filesystem at read time regardless of what
    ends up in the database.
    """
    if value is None or str(value).strip() == "":
        return None
    name = os.path.basename(str(value).strip())
    if name != str(value).strip():
        raise ValidationError("Image filename must not contain a path.")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError(
            f"Unsupported image type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}"
        )
    return name


ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def validate_image_upload(filename, data_bytes):
    """
    Validate an uploaded product image. Returns a safe filename to store under.
    Raises ValidationError on anything suspicious. Callers must still write the
    file under a fixed upload directory - this function never returns a path,
    only a filename, so path traversal via '..' or absolute paths is structurally
    impossible for the caller to introduce by using this return value directly.
    """
    if not filename:
        raise ValidationError("No file selected.")
    base = os.path.basename(filename)
    ext = os.path.splitext(base)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}"
        )
    if data_bytes is None or len(data_bytes) == 0:
        raise ValidationError("The uploaded file is empty.")
    if len(data_bytes) > MAX_IMAGE_BYTES:
        raise ValidationError("Image must be under 5 MB.")
    # Minimal magic-byte sniff so a renamed .exe can't slip through as .png.
    signatures = {
        b"\x89PNG\r\n\x1a\n": ".png",
        b"\xff\xd8\xff": ".jpg",
        b"RIFF": ".webp",
    }
    sniffed = None
    for sig, sig_ext in signatures.items():
        if data_bytes.startswith(sig):
            sniffed = sig_ext
            break
    if sniffed is None:
        raise ValidationError("File content does not look like a valid image.")
    safe_name = f"{secrets.token_hex(16)}{ext}"
    return safe_name
