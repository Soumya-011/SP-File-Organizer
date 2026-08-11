# test_gui_gallery.py — pure-logic pieces, no eel event loop required
import gui_gallery
from gui_state import APP_STATE

def test_similarity_map_not_ready_when_no_scan_yet():
    APP_STATE["cached_similar"] = None
    APP_STATE["_similar_scan_running"] = False
    result = gui_gallery.get_gallery_similarity_map(10)
    assert result["ready"] is False
    assert result["map"] == {}

def test_similarity_map_reports_scanning():
    APP_STATE["_similar_scan_running"] = True
    result = gui_gallery.get_gallery_similarity_map(10)
    assert result["scanning"] is True
    assert result["ready"] is False

def test_similarity_map_builds_correct_groups(tmp_path):
    from pathlib import Path
    APP_STATE["_similar_scan_running"] = False
    APP_STATE["cached_similar_threshold"] = 10
    APP_STATE["cached_similar"] = [
        [Path("a.jpg"), Path("b.jpg")],
        [Path("c.jpg"), Path("d.jpg"), Path("e.jpg")],
    ]
    result = gui_gallery.get_gallery_similarity_map(10)
    assert result["ready"] is True
    assert result["group_count"] == 2
    assert result["map"][str(Path("a.jpg"))] == 0
    assert result["map"][str(Path("e.jpg"))] == 1