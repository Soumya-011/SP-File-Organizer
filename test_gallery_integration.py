# test_gallery_integration.py
from PIL import Image
import gui_gallery
import cache_store
from gui_state import APP_STATE, initialize_runtime_configs
from pathlib import Path

def test_gallery_page_and_thumbnails_end_to_end(tmp_path, monkeypatch):
    # Build a fake workspace with 3 images in 2 folders
    (tmp_path / "vacation").mkdir()
    (tmp_path / "screenshots").mkdir()
    for name, folder in [("a.jpg", "vacation"), ("b.jpg", "vacation"), ("c.png", "screenshots")]:
        img = Image.new("RGB", (50, 50), color="red")
        img.save(tmp_path / folder / name)

    APP_STATE["folder"] = tmp_path
    APP_STATE["exclude_patterns"] = []
    APP_STATE["ext_to_category"] = {}
    APP_STATE["category_map"] = {}
    cache_store.init_cache_db(tmp_path)

    folders = gui_gallery.get_gallery_folders()
    assert len(folders) == 2
    assert sum(f["count"] for f in folders) == 3

    page = gui_gallery.get_gallery_page(page=0, page_size=2)
    assert page["total"] == 3
    assert page["total_pages"] == 2
    assert len(page["items"]) == 2

    paths = [i["path"] for i in page["items"]]
    thumbs = gui_gallery.get_gallery_thumbnails(paths)
    assert all(v.startswith("data:image/jpeg;base64,") for v in thumbs.values())