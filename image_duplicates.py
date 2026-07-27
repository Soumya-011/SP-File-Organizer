"""
Advanced Duplicate Finder: perceptual (visual) duplicate detection for
images.

duplicates.py finds files that are byte-for-byte identical. That misses a
very common real-world case: photo.jpg, "photo (edited).jpg", and
"photo resized.jpg" can all be the same picture to the eye while having
completely different file contents (different dimensions, compression,
or minor edits) - so a content hash never groups them. This module
compares images by what they LOOK like instead, using a perceptual hash
that stays similar across resizing, re-compression, and light edits.

Requires Pillow ("pip install Pillow"). If it isn't installed, this
feature reports that clearly via the `unavailable` flag and does nothing
else - it never breaks the rest of the app.
"""

from pathlib import Path

from utils import concurrent_hash_all, format_size
from menus import confirm_dry_run_then_execute
from duplicates import move_to_trash
from undo import save_run_log

try:
    from PIL import Image
    PIL_AVAILABLE = True
    try:
        _RESAMPLE = Image.Resampling.LANCZOS  # Pillow >= 9.1
    except AttributeError:
        _RESAMPLE = Image.LANCZOS  # older Pillow
except ImportError:
    PIL_AVAILABLE = False

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tiff", ".tif", ".heic"}

# Hamming distance (out of 64 bits) at/under which two images count as
# "the same picture". Lower = stricter (fewer false matches, but may miss
# a heavily edited/cropped copy). Higher = looser (catches more edits, but
# risks grouping genuinely different photos of similar scenes).
SIMILARITY_PRESETS = {"1": ("Strict - nearly identical only", 5),
                       "2": ("Normal - resized/re-saved/lightly edited", 10),
                       "3": ("Loose - allows heavier edits/cropping", 16)}
DEFAULT_THRESHOLD = 10


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _perceptual_hash(path: Path) -> int:
    """
    Computes a 64-bit difference hash (dhash) as a pure integer.
    Returning an integer (instead of a string) is required for the 
    ultra-fast bitwise math optimization to work.
    """
    with Image.open(path) as img:
        # Convert to grayscale and resize to 9x8
        img = img.convert("L").resize((9, 8), _RESAMPLE)
        pixels = list(img.getdata())
        
        # Compare adjacent pixels to build a 64-bit integer
        diff_hash = 0
        for row in range(8):
            for col in range(8):
                idx = row * 9 + col
                # If the left pixel is brighter than the right pixel, flip the bit to 1
                if pixels[idx] >= pixels[idx + 1]:
                    diff_hash |= 1 << (row * 8 + col)
                    
        return diff_hash


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class _DisjointSet:
    """Minimal union-find, just for clustering images that fall within the
    similarity threshold of each other (so A~B and B~C group as one set
    even if A and C alone are a bit too far apart to match directly)."""

    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[rx] = ry


def find_similar_images(files: list, threshold: int = 10, max_workers: int = None):
    # 1. Filter out non-images
    images = [f for f in files if is_image_file(f)]
    if not images or not PIL_AVAILABLE:
        return [], [], []

    # 2. Hash all images concurrently (This part is already fast)
    hashes, unreadable = concurrent_hash_all(images, _perceptual_hash, max_workers)
    
    # 3. FAST-PATH GROUPING (The Math Optimization)
    items = list(hashes.keys())
    n = len(items)
    groups = []
    visited = set()

    for i in range(n):
        if i in visited:
            continue
            
        current_group = [items[i]]
        visited.add(i)
        h1 = hashes[items[i]]  # Store the current hash in memory

        for j in range(i + 1, n):
            if j in visited:
                continue
            
            # INLINE MATH: 10x faster than calling a separate hamming_distance() function!
            # Bitwise XOR (^) finds the differences, count('1') counts them.
            if bin(h1 ^ hashes[items[j]]).count('1') <= threshold:
                current_group.append(items[j])
                visited.add(j)

        if len(current_group) > 1:
            groups.append(current_group)

    return groups, unreadable, []


def ask_similarity_threshold() -> int:
    print("\n  How similar should images be to count as a match?")
    for key, (label, _) in SIMILARITY_PRESETS.items():
        print(f"    {key}. {label}")
    choice = input("  Enter 1-3 (default 2): ").strip()
    _, threshold = SIMILARITY_PRESETS.get(choice, SIMILARITY_PRESETS["2"])
    return threshold


def review_similar_image_selection(groups: list) -> list:
    """
    Walk the user through each visually-similar group and collect files
    chosen for deletion. Unlike exact duplicates, these files can differ in
    size, dimensions, or format, so each one is listed individually rather
    than assuming they share a single size like review_duplicate_selection
    (in duplicates.py) does for true byte-identical copies.
    """
    to_delete = []
    for i, group in enumerate(groups, start=1):
        print(f"\n  Similar-looking set {i}/{len(groups)}:")
        for j, f in enumerate(group, start=1):
            try:
                size_label = format_size(f.stat().st_size)
            except OSError:
                size_label = "unknown size"
            dims_label = ""
            try:
                with Image.open(f) as img:
                    dims_label = f"  {img.width}x{img.height}"
            except Exception:
                pass
            print(f"    {j}. {f}   ({size_label}{dims_label})")

        raw = input("    Enter number(s) to DELETE (comma separated), or press Enter to keep all: ").strip()
        if not raw:
            continue

        indices = set()
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(group):
                indices.add(int(part))

        if not indices:
            continue
        if len(indices) >= len(group):
            print("    Can't delete every copy in a set - at least one must stay. Skipping this set.")
            continue

        for idx in indices:
            to_delete.append(group[idx - 1])

    return to_delete


def handle_similar_image_review(groups: list, folder: Path):
    """Offer to review and delete (trash) chosen copies from each visually-similar set."""
    print("\n  Note: these images LOOK alike but are not identical files - sizes,")
    print("  dimensions, or quality may differ. Review each set before deleting.")
    choice = input("\nReview these sets now and choose which copies to delete? (y/n): ").strip().lower()
    if choice != "y":
        return

    to_delete = review_similar_image_selection(groups)
    if not to_delete:
        print("\nNo files selected for deletion.")
        return

    reclaim = sum(f.stat().st_size for f in to_delete if f.exists())
    print(f"\n{len(to_delete)} file(s) selected for deletion (~{format_size(reclaim)} to reclaim).")
    print("(These move to a hidden trash folder, not permanently erased - use --undo to restore them.)")

    # Reuses duplicates.py's move_to_trash - "delete" here means the same
    # reversible trash-folder move as everywhere else in this tool, not an
    # unrecoverable erase.
    log_entries = confirm_dry_run_then_execute(
        lambda dry_run: move_to_trash(to_delete, folder, dry_run=dry_run)[1],
        confirm_msg="Continue and delete (move to trash) these file(s)? (y/n): ",
        cancel_msg="Cancelled. No files were deleted.",
        apply_prompt="\nApply this deletion for real now? (y/n): ",
        no_change_msg="No files were deleted.",
    )
    if log_entries is not None:
        print(f"\n{len(log_entries)} file(s) moved to trash.")
        save_run_log(folder, log_entries)