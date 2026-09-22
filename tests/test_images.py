"""
Tests for the product image pipeline (src/images.py + the write-time
validation in src/shop_db.py::create_product/update_product).

Run:  python tests/test_images.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src import images as img_mod
from src import security, shop_db


def test_all_16_demo_images_exist_on_disk():
    assert config.PRODUCT_IMAGES_DIR.exists(), "assets/products/ does not exist"
    pngs = list(config.PRODUCT_IMAGES_DIR.glob("*.png"))
    assert len(pngs) >= 16, f"expected >= 16 demo images, found {len(pngs)}"
    for p in pngs:
        assert p.stat().st_size > 0, f"{p.name} is an empty file"
    print(f"  {len(pngs)} demo product images exist on disk and are non-empty")


def test_resolve_valid_image_returns_real_path():
    images = img_mod.list_available_demo_images()
    assert images, "no demo images found to test against"
    path = img_mod.resolve_product_image(images[0])
    assert path is not None
    assert path.is_file()
    assert path.parent == config.PRODUCT_IMAGES_DIR
    print(f"  resolve_product_image('{images[0]}') -> a real file under assets/products/")


def test_resolve_none_and_empty_returns_none():
    assert img_mod.resolve_product_image(None) is None
    assert img_mod.resolve_product_image("") is None
    assert img_mod.resolve_product_image("   ") is None
    print("  None/empty image_filename resolves to None (fallback placeholder)")


def test_resolve_nonexistent_filename_returns_none():
    assert img_mod.resolve_product_image("this_file_does_not_exist.png") is None
    print("  a filename with no matching file on disk resolves to None")


def test_resolve_path_traversal_is_blocked():
    traversal_attempts = [
        "../../etc/passwd.png",
        "..\\..\\windows\\system32\\config.png",
        "/etc/passwd.png",
        "sub/dir/chef_knife.png",
    ]
    for attempt in traversal_attempts:
        result = img_mod.resolve_product_image(attempt)
        if result is not None:
            # basename() may reduce a traversal string to a real filename
            # (e.g. "../../chef_knife.png" -> "chef_knife.png") - that's
            # fine, AS LONG AS the resolved path is still safely inside
            # PRODUCT_IMAGES_DIR and not the attacker-intended target.
            assert result.parent == config.PRODUCT_IMAGES_DIR
            assert result.resolve().is_relative_to(config.PRODUCT_IMAGES_DIR.resolve())
        # else: correctly rejected outright (basename had no matching file)
    print("  path-traversal-shaped filenames never escape assets/products/")


def test_resolve_disallowed_extension_returns_none():
    assert img_mod.resolve_product_image("malware.exe") is None
    assert img_mod.resolve_product_image("script.py") is None
    assert img_mod.resolve_product_image("archive.zip") is None
    print("  disallowed file extensions resolve to None regardless of the filename")


def test_all_seeded_products_have_a_resolvable_image():
    conn = shop_db.get_connection()
    try:
        products = shop_db.search_products(conn, limit=100)
        assert products, "no products found - run scripts/init_catalog.py first"
        missing = []
        for p in products:
            path = img_mod.resolve_product_image(p.get("image_filename"))
            if path is None:
                missing.append(p["name"])
        assert not missing, f"products with no resolvable image: {missing}"
        print(f"  all {len(products)} seeded products resolve to a real image file")
    finally:
        conn.close()


def test_save_uploaded_product_image_writes_a_real_file():
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    tmp_dir = Path(tempfile.mkdtemp())
    original_dir = config.PRODUCT_IMAGES_DIR
    try:
        config.PRODUCT_IMAGES_DIR = tmp_dir
        saved_name = img_mod.save_uploaded_product_image("my_photo.png", png_bytes)
        assert (tmp_dir / saved_name).is_file()
        assert saved_name != "my_photo.png", "should be renamed to a safe random name"
        resolved = img_mod.resolve_product_image(saved_name)
        assert resolved is not None and resolved.is_file()
    finally:
        config.PRODUCT_IMAGES_DIR = original_dir
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("  uploaded image is saved under a safe random filename and then resolves correctly")


def test_save_uploaded_product_image_rejects_bad_upload():
    tmp_dir = Path(tempfile.mkdtemp())
    original_dir = config.PRODUCT_IMAGES_DIR
    try:
        config.PRODUCT_IMAGES_DIR = tmp_dir
        try:
            img_mod.save_uploaded_product_image("evil.exe", b"MZ\x90\x00fake")
            assert False, "expected ValidationError"
        except security.ValidationError:
            pass
        assert list(tmp_dir.iterdir()) == [], "rejected upload must not write any file"
    finally:
        config.PRODUCT_IMAGES_DIR = original_dir
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("  a rejected upload writes nothing to disk")


def test_create_product_rejects_path_in_image_filename():
    conn = shop_db.get_connection()
    try:
        seller = shop_db.register_user(
            conn, "imgtest_seller", "imgtest_seller@example.com", "GoodPass123!",
            "SELLER", "Image Test Seller", "Image Test Shop",
        )
        try:
            shop_db.create_product(
                conn, seller, "Bad Image Product", "desc", 5.0, 1, None,
                image_filename="../../etc/passwd.png",
            )
            assert False, "expected ValidationError for a path in image_filename"
        except security.ValidationError:
            pass
    finally:
        conn.execute("DELETE FROM users WHERE username = 'imgtest_seller'")
        conn.commit()
        conn.close()
    print("  create_product rejects an image_filename containing a path separator")


def test_create_product_with_valid_demo_image_succeeds():
    conn = shop_db.get_connection()
    try:
        seller = shop_db.register_user(
            conn, "imgtest_seller2", "imgtest_seller2@example.com", "GoodPass123!",
            "SELLER", "Image Test Seller Two", "Image Test Shop Two",
        )
        pid = shop_db.create_product(
            conn, seller, "Good Image Product", "desc", 5.0, 1, None,
            image_filename="chef_knife.png",
        )
        product = shop_db.get_product(conn, pid)
        assert product["image_filename"] == "chef_knife.png"
        path = img_mod.resolve_product_image(product["image_filename"])
        assert path is not None and path.is_file()
    finally:
        conn.execute("DELETE FROM users WHERE username = 'imgtest_seller2'")
        conn.commit()
        conn.close()
    print("  create_product accepts a valid demo image filename and it resolves correctly")


def test_clean_image_url_accepts_valid_https():
    url = security.clean_image_url("https://example.com/photos/knife.jpg")
    assert url == "https://example.com/photos/knife.jpg"
    print("  a well-formed https:// URL is accepted unchanged")


def test_clean_image_url_rejects_bad_schemes():
    for bad in (
        "javascript:alert(1)",
        "file:///etc/passwd",
        "ftp://example.com/x.jpg",
        "data:image/png;base64,AAAA",
    ):
        try:
            security.clean_image_url(bad)
            assert False, f"expected rejection of {bad!r}"
        except security.ValidationError:
            pass
    print("  javascript:/file:/ftp:/data: schemes are all rejected")


def test_clean_image_url_rejects_ssrf_shaped_hosts():
    for bad in (
        "http://localhost/x.jpg",
        "http://127.0.0.1/x.jpg",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.5/x.jpg",
        "http://10.0.0.1/x.jpg",
    ):
        try:
            security.clean_image_url(bad)
            assert False, f"expected rejection of {bad!r}"
        except security.ValidationError:
            pass
    print("  localhost/loopback/private-network/cloud-metadata hosts are all rejected")


def test_clean_image_url_rejects_empty_and_none():
    assert security.clean_image_url(None) is None
    assert security.clean_image_url("") is None
    assert security.clean_image_url("   ") is None
    print("  empty/None image URL cleanly means 'no URL', not an error")


def test_resolve_product_display_prefers_local_file_over_url():
    product = {
        "image_filename": "chef_knife.png",
        "image_url": "https://example.com/some-other-photo.jpg",
        "name": "Test Product",
    }
    source, alt = img_mod.resolve_product_display(product)
    assert isinstance(source, Path)
    assert source.name == "chef_knife.png"
    print("  when both a local file and a URL are set, the local file wins")


def test_resolve_product_display_falls_back_to_url_when_no_local_file():
    product = {
        "image_filename": None,
        "image_url": "https://example.com/photos/knife.jpg",
        "name": "Test Product",
    }
    source, alt = img_mod.resolve_product_display(product)
    assert source == "https://example.com/photos/knife.jpg"
    print("  with no local file, a valid stored URL is used")


def test_resolve_product_display_returns_none_for_nothing():
    product = {"image_filename": None, "image_url": None, "name": "Test Product"}
    source, alt = img_mod.resolve_product_display(product)
    assert source is None
    assert alt == "Test Product"
    print("  with neither a file nor a URL, source is None and alt falls back to the product name")


def test_update_product_can_set_and_clear_image_url():
    conn = shop_db.get_connection()
    try:
        seller = shop_db.register_user(
            conn, "imgtest_seller3", "imgtest_seller3@example.com", "GoodPass123!",
            "SELLER", "Image Test Seller Three", "Image Test Shop Three",
        )
        pid = shop_db.create_product(
            conn, seller, "URL Product", "desc", 5.0, 1, None,
        )
        shop_db.update_product(
            conn, pid, seller, image_url="https://example.com/x.jpg"
        )
        product = shop_db.get_product(conn, pid)
        assert product["image_url"] == "https://example.com/x.jpg"

        try:
            shop_db.update_product(conn, pid, seller, image_url="http://localhost/x.jpg")
            assert False, "expected rejection of an SSRF-shaped URL on update"
        except security.ValidationError:
            pass
    finally:
        conn.execute("DELETE FROM users WHERE username = 'imgtest_seller3'")
        conn.commit()
        conn.close()
    print("  update_product validates image_url the same way create_product does")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main():
    failed = 0
    print("Running TrustCatalog image-pipeline tests\n")
    for test in TESTS:
        name = test.__name__
        try:
            test()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:                     # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} tests passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
