"""
Product image pipeline.

Storage model: catalog_products.image_filename holds ONLY a bare filename
(e.g. "chef_knife.png") - never a path, never a URL. Every real file lives
in exactly one place: config.PRODUCT_IMAGES_DIR (assets/products/). This
module is the single place that turns a stored filename into something
Streamlit can actually render, and the single place that turns an
uploaded file into something safe to store.

Why this is safe against path traversal: resolve_product_image() strips
everything except the basename before it ever touches the filesystem.
os.path.basename("../../etc/passwd.png") returns "passwd.png" - there is
no separator character left for ".." to act on, so the lookup can only
ever land inside PRODUCT_IMAGES_DIR. This holds regardless of what string
is stored in the database or typed into a form field.
"""

import os

import config
from src import security


def resolve_product_image(image_filename):
    """
    Return a Path to a real, existing, safe LOCAL image file for this
    product, or None if no valid local image is available. Kept as a
    separate function (rather than folded into resolve_product_display)
    because callers that specifically need "is there a bundled local
    file" (e.g. the demo-image picker) still call this directly.
    """
    if not image_filename:
        return None

    name = os.path.basename(str(image_filename).strip())
    if not name or name in (".", ".."):
        return None

    ext = os.path.splitext(name)[1].lower()
    if ext not in security.ALLOWED_IMAGE_EXTENSIONS:
        return None

    candidate = config.PRODUCT_IMAGES_DIR / name
    if candidate.parent != config.PRODUCT_IMAGES_DIR:
        return None  # defensive: should be structurally impossible given basename() above
    if not candidate.is_file():
        return None
    return candidate


def resolve_product_display(product):
    """
    The single function pages should call to decide what to show for a
    product. Returns (source, alt_text):

      source  - a Path (local file, preferred - works fully offline) or a
                str URL (only when no local file exists and a validated
                image_url is stored), or None if nothing is available.
      alt_text - the stored alt text, or a sensible default built from the
                 product name, for accessibility (passed to st.image's
                 `caption` - Streamlit has no dedicated `alt` parameter).

    Local files are always preferred over a URL: they render identically
    with or without internet access, which matters for this offline-first
    demo. A URL is only used as a fallback when no local file is set.
    """
    local_path = resolve_product_image(product.get("image_filename"))
    alt_text = (product.get("image_alt_text") or "").strip() or product.get("name", "Product")

    if local_path is not None:
        return local_path, alt_text

    url = product.get("image_url")
    if url:
        try:
            validated = security.clean_image_url(url)
        except security.ValidationError:
            return None, alt_text
        return validated, alt_text

    return None, alt_text


def save_uploaded_product_image(filename, data_bytes):
    """
    Validate and persist an uploaded product image (from st.file_uploader).
    Returns the safe filename to store in catalog_products.image_filename.
    Raises security.ValidationError on anything invalid - callers should
    show that message to the user rather than let the upload silently fail.
    """
    safe_name = security.validate_image_upload(filename, data_bytes)
    target = config.PRODUCT_IMAGES_DIR / safe_name
    target.write_bytes(data_bytes)
    return safe_name


def list_available_demo_images():
    """
    The curated set of generated demo images, for a 'pick an existing demo
    image' dropdown in Seller Center - an easy option alongside uploading
    a real file.
    """
    if not config.PRODUCT_IMAGES_DIR.exists():
        return []
    return sorted(
        p.name for p in config.PRODUCT_IMAGES_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in security.ALLOWED_IMAGE_EXTENSIONS
    )
