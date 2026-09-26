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

PERFORMANCE (#6): _perceptual_hash() bit-packing is now fully vectorized
with numpy, no Python double-loop. Outputs are byte-identical to the old
implementation so existing cached hashes in .cache_store.db remain valid.

PERFORMANCE (#5): _perceptual_hash() uses numpy arrays and vectorized
comparison instead of Python list/loop.

PERFORMANCE (#4): Hash results are cached to SQLite via cache_store.py,
so unchanged images skip rehashing on subsequent sessions.

PERFORMANCE (app-lighter): Pillow and numpy are now imported LAZILY (first
actual use) rather than at module load. gui.py imports every endpoint
module unconditionally at startup to register their @eel.expose handlers —
without this, both libraries (~19 MB combined, measured) would load into
every session's memory even when similar-image detection is never touched
that session. Both are used exclusively within _perceptual_hash() and
find_similar_images(), so deferring them costs nothing on the actual
scanning path — see _ensure_pil()/_ensure_numpy() below.
"""

from pathlib import Path
from collections import defaultdict

from utils import concurrent_hash_all, format_size
from menus import confirm_dry_run_then_execute
from duplicates import move_to_trash
from undo import save_run_log

_PIL_Image = None
_PIL_AVAILABLE = None
_RESAMPLE = None


_PILLOW_VARIANT = None  # "simd" | "standard" | None (not yet detected)


def _ensure_pil():
    """Import Pillow on first use only. Returns True if available. Pillow
    has always been a genuinely optional dependency here (see the
    PIL_AVAILABLE-style checks throughout) — this just defers paying for it
    until it's actually needed instead of on every app launch.

    Also detects whether the installed Pillow is the SIMD-accelerated
    variant (pillow-simd). There's nothing to branch on in code here —
    pillow-simd is a drop-in replacement that installs into the same `PIL`
    import namespace as stock Pillow (you can't have both installed at
    once), so which one is active is purely a `pip install` choice, not
    something this function can pick between at runtime. This just reports
    which one ended up active, so it's visible without guessing.
    """
    global _PIL_Image, _PIL_AVAILABLE, _RESAMPLE, _PILLOW_VARIANT
    if _PIL_AVAILABLE is not None:
        return _PIL_AVAILABLE
    try:
        from PIL import Image
        _PIL_Image = Image
        try:
            _RESAMPLE = Image.Resampling.LANCZOS  # Pillow >= 9.1
        except AttributeError:
            _RESAMPLE = Image.LANCZOS  # older Pillow
        _PIL_AVAILABLE = True

        try:
            import PIL
            # pillow-simd's version string carries a ".postN" suffix
            # (e.g. "9.0.0.post1") that stock Pillow's never does — the
            # commonly used way to tell them apart at runtime.
            _PILLOW_VARIANT = "simd" if "post" in PIL.__version__ else "standard"
            print(f"  Image library: Pillow-SIMD ({PIL.__version__}) — accelerated resize/decode"
                  if _PILLOW_VARIANT == "simd" else
                  f"  Image library: standard Pillow ({PIL.__version__}). "
                  f"Install pillow-simd instead for faster similar-image scans, if a wheel exists for your platform.")
        except Exception:
            _PILLOW_VARIANT = None
    except ImportError:
        _PIL_AVAILABLE = False
    return _PIL_AVAILABLE


_np = None
_POPCOUNT_TABLE = None


def _ensure_numpy():
    """Import numpy on first use only, and build the popcount lookup table
    at that point too (it depends on numpy, so it can't be built at module
    load either without forcing the eager import back in)."""
    global _np, _POPCOUNT_TABLE
    if _np is not None:
        return _np
    import numpy as np
    _np = np
    _POPCOUNT_TABLE = np.array([bin(i).count('1') for i in range(256)], dtype=np.uint8)
    return _np


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tiff", ".tif", ".heic"}

# Hamming distance (out of 64 bits) at/under which two images count as
# "the same picture". Lower = stricter (fewer false matches, but may miss
# a heavily edited/cropped copy). Higher = looser (catches more edits, but
# risks grouping genuinely different photos of similar scenes).
SIMILARITY_PRESETS = {"1": ("Strict - nearly identical only", 5),
                       "2": ("Normal - resized/re-saved/lightly edited", 10),
                       "3": ("Loose - allows heavier edits/cropping", 16)}
DEFAULT_THRESHOLD = 10

# LSH banding constants for Fix 5.
# Split each 64-bit hash into LSH_NUM_BANDS bands of LSH_BAND_BITS bits each.
# 64 = LSH_NUM_BANDS * LSH_BAND_BITS. Tuning: more bands = higher recall but
# slower; fewer bands = faster but more missed matches.
LSH_NUM_BANDS = 4
LSH_BAND_BITS = 16  # 4 * 16 = 64


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _perceptual_hash(path: Path) -> int:
    """
    Computes a 64-bit difference hash (dhash) as a pure integer.

    Fix 6: bit-packing is now fully vectorized with numpy — no Python
    double-loop. Bit ordering is identical to the original implementation:
    bit (row*8 + col) corresponds to diff[row, col].
    Outputs are byte-identical so existing cached hashes remain valid.
    """
    np = _ensure_numpy()
    with _PIL_Image.open(path) as img:
        img = img.convert("L").resize((9, 8), _RESAMPLE)
        pixels = np.array(img.getdata(), dtype=np.uint8)
        grid = pixels.reshape(8, 9)
        diff = grid[:, :-1] >= grid[:, 1:]          # shape (8, 8), vectorized
        # Pre-computed weight matrix: bit (row*8+col) has weight 2^(row*8+col)
        weights = (1 << np.arange(64, dtype=np.uint64)).reshape(8, 8)
        hash_val = int((diff.astype(np.uint64) * weights).sum())
        return hash_val


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _color_signature(path: Path):
    """Mean RGB (16x16 downsample) — a confirmation signal that's
    genuinely independent of the dhash check, not just a second coarse
    grayscale measure. dhash converts to grayscale BEFORE hashing, so it
    is completely blind to color: two images with the same coarse
    light/dark gradient structure but visibly different colors (two
    different sunset photos, a red product shot vs a blue one, etc.) can
    still collide on dhash. An average-hash confirmation doesn't fully fix
    this, because it's also a coarse grayscale brightness measure and
    shares the same blind spot dhash has — color is the axis dhash
    actually discards, so that's what the confirmation check should test.
    """
    np = _ensure_numpy()
    with _PIL_Image.open(path) as img:
        img = img.convert("RGB").resize((16, 16), _RESAMPLE)
        arr = np.array(img, dtype=np.float32).reshape(-1, 3)
        return tuple(float(x) for x in arr.mean(axis=0))


def _color_distance(sig1, sig2) -> float:
    """Euclidean distance between two mean-RGB signatures, each channel
    0-255. Real near-duplicates (the same photo re-saved, resized, or
    lightly re-compressed) land well under 10; two genuinely different
    photos that only coincidentally share dhash's grayscale gradient
    structure typically differ by 30+."""
    return sum((a - b) ** 2 for a, b in zip(sig1, sig2)) ** 0.5


# Distance (0-255 scale per channel) at/under which two images' mean-RGB
# colors count as "close enough to be the same photo". Deliberately not
# tied to the person's Strict/Normal/Loose Hamming-distance choice — color
# drift from real re-compression is small and fairly constant regardless
# of how loosely they want dhash structure matching, so a single fixed
# threshold here is more predictable than scaling it with the other value.
_COLOR_CONFIRM_THRESHOLD = 25.0


def _color_sig_for_item(item: Path, sig_memo: dict):
    """Lazily compute/cache the color-signature confirmation value for one
    item, memoized within a single find_similar_images() call and
    persisted to cache_store's hash_cache table (hash_type="colorsig",
    stored as a comma-joined string since it's 3 floats, not an int hash)
    so repeat scans don't recompute it either. Returns None if the file
    can't be read — callers treat that as "can't confirm, don't group".
    """
    if item in sig_memo:
        return sig_memo[item]

    try:
        import cache_store
        db_available = cache_store._DB_PATH is not None
    except (ImportError, AttributeError):
        cache_store = None
        db_available = False

    mtime = size = None
    try:
        st = item.stat()
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        sig_memo[item] = None
        return None

    value = None
    if db_available:
        try:
            cached = cache_store.get_cached_hash(item, "colorsig", mtime, size)
            if cached is not None:
                value = tuple(float(x) for x in str(cached).split(","))
        except Exception:
            pass

    if value is None:
        try:
            value = _color_signature(item)
        except Exception:
            value = None
        if value is not None and db_available:
            try:
                cache_store.put_cached_hash(item, "colorsig", mtime, size,
                                             ",".join(str(x) for x in value))
            except Exception:
                pass

    sig_memo[item] = value
    return value


def _lsh_candidate_pairs(items, hash_array, num_bands=LSH_NUM_BANDS,
                           band_bits=LSH_BAND_BITS):
    """Yield candidate (i, j) index pairs likely to be within threshold,
    using LSH banding on the 64-bit dhash.

    Split each 64-bit hash into `num_bands` bands of `band_bits` bits.
    Images that share at least one band value are candidate pairs.
    Only those pairs get the exact Hamming-distance check.

    Recall trade-off: two images within threshold can theoretically land
    in zero shared bands if the differing bits spread across bands unluckily.
    This is the standard acceptable property of LSH used in production
    perceptual-dedup systems — not a bug.

    Known characteristic: within a single bucket, pairwise comparison is
    O(m^2) where m is the bucket size. This can be noticeable on folders
    with hundreds of near-identical burst-mode screenshots landing in the
    same band — expected LSH behavior, not worth pre-optimizing.
    """
    n = len(items)
    buckets = defaultdict(list)
    for band in range(num_bands):
        shift = band * band_bits
        mask = (1 << band_bits) - 1
        for i in range(n):
            band_val = (int(hash_array[i]) >> shift) & mask
            buckets[(band, band_val)].append(i)
    seen_pairs = set()
    for bucket_indices in buckets.values():
        if len(bucket_indices) < 2:
            continue
        for a in range(len(bucket_indices)):
            for b in range(a + 1, len(bucket_indices)):
                pair = (bucket_indices[a], bucket_indices[b])
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    yield pair


class _StarClusters:
    """Greedy "star" clustering: replaces plain union-find, which chains
    matches transitively (A~B and B~C groups A with C even if A and C look
    nothing alike — the "bridge" false-grouping bug). Here, a cluster is a
    hub-and-spoke: every member must be DIRECTLY confirmed-similar
    (Hamming + color, both passed) to that cluster's one center. Nothing
    propagates through an intermediate member, so two genuinely dissimilar
    images can never end up in the same group just because each happens to
    resemble something in between.

    Trade-off, by design: a node whose only confirmed matches were already
    claimed as some other center's satellites is left out of every group
    (rather than forming a long chain) — a tighter, more accurate result at
    the cost of occasionally splitting what a human might call one loose
    family of near-duplicates into two. That is the intended effect, not a
    bug: it is what actually stops unrelated photos from being merged.
    """

    def __init__(self, items):
        self.adjacency = defaultdict(set)
        self._order = {item: i for i, item in enumerate(items)}

    def add_edge(self, a, b):
        self.adjacency[a].add(b)
        self.adjacency[b].add(a)

    def build_clusters(self):
        assigned = set()
        clusters = []
        # Highest-degree nodes (most direct confirmed matches) make the
        # best centers; stable-sort ties keep original scan order.
        by_degree = sorted(self.adjacency.keys(), key=lambda x: (-len(self.adjacency[x]), self._order[x]))

        for center in by_degree:
            if center in assigned:
                continue
            neighbors = [n for n in self.adjacency[center] if n not in assigned]
            if not neighbors:
                continue
            cluster = [center] + neighbors
            assigned.add(center)
            assigned.update(neighbors)
            clusters.append(cluster)

        return clusters


def find_similar_images(files: list, threshold: int = 10, max_workers: int = None,
                         progress_callback=None, size_cache: dict = None):
    """Returns (groups, unreadable, unavailable). `unavailable` is True only
    when Pillow itself isn't installed - distinct from "no images found" or
    "no matches found", both of which are legitimate empty results.

    PERFORMANCE (#4): Checks cache_store for previously computed hashes.
    Only uncached images are hashed, then all new hashes are batch-stored.

    PERFORMANCE (#5b — LSH): Grouping uses LSH banding instead of O(n^2)
    exhaustive pairwise comparison. Only candidate pairs that share at
    least one band are compared with exact Hamming distance.

    ACCURACY: Confirmed-similar pairs are clustered with star clustering
    (hub-and-spoke), not transitive union-find — see _StarClusters above
    for why: it's what stops unrelated photos from being merged through a
    "bridge" chain of individually-plausible matches.

    PERFORMANCE (Phase 2): If this exact image population + threshold matches
    a previously completed scan (see cache_store.compute_scan_signature),
    the walk/hash/LSH-grouping pipeline is skipped entirely and the stored
    result is returned directly. size_cache, if supplied, lets the
    signature be computed with zero extra stat() calls.

    Args:
        progress_callback: optional callable(pct, message, done, total).
            pct is 0-100. Called during hashing and grouping phases.
    """
    if not _ensure_pil():
        return [], [], True

    # 1. Filter out non-images
    images = [f for f in files if is_image_file(f)]
    if not images:
        return [], [], False

    # Ensure numpy (and _POPCOUNT_TABLE, which depends on it) is loaded
    # before any of the array code below runs — including the case where
    # every image hits the hash cache and _perceptual_hash() (which also
    # calls _ensure_numpy()) never actually runs this session.
    np = _ensure_numpy()

    # PERFORMANCE (Phase 2): coarse result-level cache, checked before any
    # hashing happens.
    try:
        import cache_store
        _result_cache_available = cache_store._DB_PATH is not None
    except (ImportError, AttributeError):
        _result_cache_available = False

    _signature = None
    _scan_type = f"similar_images_{threshold}"
    if _result_cache_available:
        _signature = cache_store.compute_scan_signature(images, size_cache=size_cache)
        _cached = cache_store.get_cached_scan_result(_scan_type, _signature)
        if _cached is not None:
            _groups_paths, _unreadable_count = _cached
            groups = [[Path(p) for p in g] for g in _groups_paths]
            print(f"  [scan cache] Loaded {len(groups)} cached similar-image group(s) "
                  f"(signature {_signature}, threshold {threshold}) — skipped full rescan.")
            if progress_callback:
                progress_callback(100, f"Loaded {len(groups)} cached similar-image group(s).", 0, 0)
            return groups, [], False

    if progress_callback:
        progress_callback(2, f"Found {len(images)} images to analyze...", 0, len(images))

    # 2. Try loading hashes from cache (#4)
    try:
        import cache_store
        cache_available = True
    except ImportError:
        cache_available = False

    cached_hashes = {}
    uncached_images = []

    if cache_available and cache_store._DB_PATH is not None:
        batch_keys = []
        stat_cache = {}  # path -> (mtime, size) — avoid triple stat() per image
        for f in images:
            try:
                st = f.stat()
                stat_cache[f] = (st.st_mtime, st.st_size)
                batch_keys.append((str(f), "phash", st.st_mtime, st.st_size))
            except OSError:
                uncached_images.append(f)

        cached_results = cache_store.get_cached_hashes_batch(batch_keys)
        for f in images:
            if f in stat_cache:
                mtime, size = stat_cache[f]
                key = (str(f), "phash", mtime, size)
                if key in cached_results:
                    cached_hashes[f] = int(cached_results[key])
                else:
                    if f not in uncached_images:
                        uncached_images.append(f)
            else:
                if f not in uncached_images:
                    uncached_images.append(f)
    else:
        uncached_images = images[:]

    if progress_callback:
        cached_count = len(cached_hashes)
        total_count = len(images)
        if cached_count > 0:
            progress_callback(5, f"Loaded {cached_count} cached hashes, {total_count - cached_count} to compute...", 0, total_count - cached_count)
        else:
            progress_callback(5, f"Computing perceptual hashes for {len(uncached_images)} images...", 0, len(uncached_images))

    # 3. Hash only uncached images concurrently
    new_hashes, unreadable = {}, []
    if uncached_images:
        def _hash_progress(done, total):
            if progress_callback:
                pct = 5 + int(70 * done / total) if total > 0 else 5
                progress_callback(pct, f"Hashing images... {done}/{total}", done, total)

        new_hashes, unreadable = concurrent_hash_all(
            uncached_images, _perceptual_hash, max_workers,
            use_process_pool=False,
            progress_callback=_hash_progress)

    if progress_callback:
        progress_callback(78, "LSH bucketing for candidate pairs...", 0, 0)

    # Merge cached + new
    all_hashes = {}
    all_hashes.update(cached_hashes)
    all_hashes.update(new_hashes)

    # Batch-store new hashes (#4)
    if cache_available and cache_store._DB_PATH is not None and new_hashes:
        store_entries = []
        for f, h in new_hashes.items():
            try:
                if f in stat_cache:
                    mtime, size = stat_cache[f]
                else:
                    st = f.stat()
                    mtime, size = st.st_mtime, st.st_size
                store_entries.append((str(f), "phash", mtime, size, h))
            except OSError:
                pass
        if store_entries:
            cache_store.put_cached_hashes_batch(store_entries)

    # 4. LSH-BASED GROUPING (Fix 5 — replaces O(n^2) exhaustive scan)
    items = list(all_hashes.keys())
    n = len(items)
    if n == 0:
        return [], unreadable, False

    hash_array = np.array([all_hashes[item] for item in items], dtype=np.uint64)

    # Phase 4a: LSH bucketing — find candidate pairs
    if progress_callback:
        progress_callback(80, "LSH bucketing...", 0, 0)

    ds = _StarClusters(items)
    pairs_checked = 0
    matches_found = 0
    total_candidate_pairs = 0
    color_rejected = 0
    colorsig_memo = {}

    for i, j in _lsh_candidate_pairs(items, hash_array):
        total_candidate_pairs += 1
        # Exact Hamming distance using numpy XOR + popcount table
        xor_val = int(hash_array[i]) ^ int(hash_array[j])
        xor_bytes = np.array([xor_val], dtype=np.uint64).view(np.uint8).reshape(1, 8)
        dist = int(_POPCOUNT_TABLE[xor_bytes].sum())
        pairs_checked += 1
        if dist <= threshold:
            # dHash passed — confirm with color before committing to a
            # group. This is what stops "two unrelated photos with similar
            # composition" false positives: dHash converts to grayscale
            # before hashing, so two differently-colored photos with the
            # same coarse light/dark layout can pass it undetected. Color
            # is exactly the signal dHash discards, making this check
            # genuinely independent rather than another coarse grayscale
            # brightness measure with the same blind spot.
            c1 = _color_sig_for_item(items[i], colorsig_memo)
            c2 = _color_sig_for_item(items[j], colorsig_memo)
            if c1 is None or c2 is None or _color_distance(c1, c2) > _COLOR_CONFIRM_THRESHOLD:
                color_rejected += 1
                continue
            ds.add_edge(items[i], items[j])
            matches_found += 1

    if progress_callback:
        progress_callback(95, f"Building groups from {matches_found} matches "
                               f"({color_rejected} rejected by color confirmation)...",
                           pairs_checked, total_candidate_pairs)

    # Phase 4b: Build final groups from the star-clustering structure —
    # every member is directly confirmed-similar to its cluster's center,
    # so no transitive "bridge" chains survive into the result.
    groups = ds.build_clusters()

    if _result_cache_available and _signature:
        try:
            cache_store.put_cached_scan_result(
                _scan_type, _signature,
                [[str(p) for p in g] for g in groups],
                len(unreadable))
            print(f"  [scan cache] Stored {len(groups)} similar-image group(s) "
                  f"under signature {_signature}.")
        except Exception:
            pass

    if progress_callback:
        progress_callback(100, f"Found {len(groups)} similar groups ({total_candidate_pairs} candidate pairs checked)", n, n)

    return groups, unreadable, False


def get_group_min_distances(groups: list) -> dict:
    """For every file across `groups`, its minimum Hamming distance to
    another file in the SAME group — the "how close is this actually" number
    Phase 2 promised to surface in the UI instead of a trust-me binary
    label. Borderline matches (e.g. found only at the Loose threshold)
    become visibly distinguishable from near-exact ones.

    Reads phash values back from the SQLite hash_cache (already populated
    by find_similar_images() for every image it hashed) rather than
    recomputing anything — cheap, and works identically whether `groups`
    came from a live scan or a disk-cached scan result. Returns {} if the
    cache isn't available, or a file's hash isn't in it for any reason
    (e.g. it was deleted since the scan) - callers should treat a missing
    entry as "distance unknown", not zero.
    """
    try:
        import cache_store
        if cache_store._DB_PATH is None:
            return {}
    except (ImportError, AttributeError):
        return {}

    all_files = [f for g in groups for f in g]
    if not all_files:
        return {}

    batch_keys = []
    stat_cache = {}
    for f in all_files:
        try:
            st = f.stat()
            stat_cache[f] = (st.st_mtime, st.st_size)
            batch_keys.append((str(f), "phash", st.st_mtime, st.st_size))
        except OSError:
            continue

    cached = cache_store.get_cached_hashes_batch(batch_keys)
    hashes = {}
    for f in all_files:
        if f in stat_cache:
            key = (str(f), "phash")
            if key in cached:
                hashes[f] = int(cached[key])

    distances = {}
    for group in groups:
        members = [f for f in group if f in hashes]
        for f in members:
            best = min(
                (hamming_distance(hashes[f], hashes[other]) for other in members if other is not f),
                default=None,
            )
            if best is not None:
                distances[str(f)] = best
    return distances


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
    chosen for deletion.
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
                with _PIL_Image.open(f) as img:
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

    log_entries = confirm_dry_run_then_execute(
        lambda dry_run: move_to_trash(to_delete, folder, dry_run=dry_run)[1],
        confirm_msg="Continue and delete (move to trash) these file(s)? (y/n): ",
        cancel_msg="Cancelled. No files were deleted.",
        apply_prompt="\nApply this for real now? (y/n): ",
        no_change_msg="No files were deleted.",
    )
    if log_entries is not None:
        print(f"\n{len(log_entries)} file(s) moved to trash.")
        save_run_log(folder, log_entries)