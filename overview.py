"""
The overview panel: total file counts/sizes, breakdowns by type and by
category, and a duplicate-content summary. Purely a reporting layer - it
doesn't scan or move anything itself.
"""

from pathlib import Path

from utils import format_size


def compute_category_sizes(files_by_category: dict) -> dict:
    sizes = {}
    for category, files in files_by_category.items():
        total = 0
        for f in files:
            try:
                total += f.stat().st_size
            except OSError:
                pass
        sizes[category] = total
    return sizes


def print_overview(folder: Path, ext_counts: dict, ext_sizes: dict, files_by_category: dict,
                    excluded_count: int, duplicate_groups: list, unreadable_files: list = None):
    unreadable_files = unreadable_files or []
    total_files = sum(ext_counts.values())
    total_size = sum(ext_sizes.values())

    print("\n" + "=" * 50)
    print(f"  FOLDER OVERVIEW: {folder}")
    print("=" * 50)

    if not ext_counts:
        print("  (nothing scannable here)")
        if excluded_count:
            print(f"  ({excluded_count} file(s) excluded from scan)")
        return

    print(f"  Total files: {total_files}    Total size: {format_size(total_size)}")
    if excluded_count:
        print(f"  ({excluded_count} file(s) excluded from scan - script/config/pattern matches)")

    print("\n  By file type:")
    print("  " + "-" * 44)
    for ext, count in sorted(ext_counts.items(), key=lambda x: -ext_sizes.get(x[0], 0)):
        print(f"    {ext:<15} {count:>4} file(s)   {format_size(ext_sizes[ext]):>10}")

    print("\n  By category:")
    print("  " + "-" * 44)
    category_sizes = compute_category_sizes(files_by_category)
    for category, files in sorted(files_by_category.items(), key=lambda x: -category_sizes.get(x[0], 0)):
        print(f"    {category:<15} {len(files):>4} file(s)   {format_size(category_sizes[category]):>10}")

    print("\n  Duplicate files:")
    print("  " + "-" * 44)
    if duplicate_groups:
        extra_copies = sum(len(g) - 1 for g in duplicate_groups)
        reclaimable = sum((len(g) - 1) * g[0].stat().st_size for g in duplicate_groups)
        print(f"    {len(duplicate_groups)} duplicate set(s) found -> {extra_copies} extra "
              f"copy/copies, ~{format_size(reclaimable)} reclaimable")
        preview = duplicate_groups[:5]
        for group in preview:
            names = ", ".join(p.name for p in group)
            print(f"      - {names}")
        if len(duplicate_groups) > 5:
            print(f"      ... and {len(duplicate_groups) - 5} more set(s)")
        print("    (Duplicates are only reported here, not auto-deleted - review before removing anything.)")
    else:
        print("    None found.")

    if unreadable_files:
        print(f"\n  Note: {len(unreadable_files)} file(s) could NOT be checked for duplicates")
        print("  (locked by another program, permission denied, or similar) - these were")
        print("  skipped, so a real duplicate involving them could be missed:")
        for f in unreadable_files[:10]:
            print(f"      - {f}")
        if len(unreadable_files) > 10:
            print(f"      ... and {len(unreadable_files) - 10} more")
        print("  Close any program using these files (or run as administrator) and re-run to include them.")

    print("=" * 50)
