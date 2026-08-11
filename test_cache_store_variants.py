# test_cache_store_variants.py
import cache_store
from pathlib import Path

def test_thumbnail_variants_do_not_collide(tmp_path):
    cache_store.init_cache_db(tmp_path)
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"fake")
    mtime, size = 1000.0, 4

    cache_store.put_cached_thumb(f, mtime, size, "DATA_DUP_ROW", variant="dup_row")
    cache_store.put_cached_thumb(f, mtime, size, "DATA_GALLERY", variant="gallery")

    assert cache_store.get_cached_thumb(f, mtime, size, variant="dup_row") == "DATA_DUP_ROW"
    assert cache_store.get_cached_thumb(f, mtime, size, variant="gallery") == "DATA_GALLERY"

def test_batch_thumbs_scoped_to_variant(tmp_path):
    cache_store.init_cache_db(tmp_path)
    entries = [("a.jpg", 1.0, 10, "X"), ("b.jpg", 2.0, 20, "Y")]
    cache_store.put_cached_thumbs_batch(entries, variant="gallery")

    dup_row_result = cache_store.get_cached_thumbs_batch([("a.jpg", 1.0, 10)], variant="dup_row")
    gallery_result = cache_store.get_cached_thumbs_batch([("a.jpg", 1.0, 10)], variant="gallery")

    assert dup_row_result == {}          # not visible under the wrong variant
    assert gallery_result == {"a.jpg": "X"}