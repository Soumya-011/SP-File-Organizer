"""
Storage Analyzer: where disk space is going, broken down by category.

Reuses overview.py's per-category size math (so the numbers always match
what the Overview panel reports) and adds bar-chart rendering on top of it
- an ASCII version for the text menu, and the sizing math the GUI's Canvas
bars use so both stay in sync.
"""

from overview import compute_category_sizes
from utils import format_size


def compute_storage_usage(files_by_category: dict) -> dict:
    """{category: total_bytes}, largest first."""
    sizes = compute_category_sizes(files_by_category)
    return dict(sorted(sizes.items(), key=lambda kv: -kv[1]))


def ascii_bar_chart(category_sizes: dict, bar_width: int = 30) -> str:
    """
    Renders category_sizes as stacked text blocks:

        Videos
        18.0 GB
        ██████████

    Every bar is scaled relative to the largest category, so the biggest
    consumer always fills the full `bar_width`.
    """
    if not category_sizes or not any(category_sizes.values()):
        return "  (nothing to show)"

    max_size = max(category_sizes.values()) or 1
    lines = []
    for category, size in category_sizes.items():
        filled = max(1, round((size / max_size) * bar_width)) if size else 0
        bar = "\u2588" * filled
        lines.append(f"  {category}\n  {format_size(size)}\n  {bar}\n")
    return "\n".join(lines)


def print_storage_analyzer(category_sizes: dict):
    print("\n" + "=" * 50)
    print("  STORAGE ANALYZER")
    print("=" * 50)
    print(ascii_bar_chart(category_sizes))
    print("=" * 50)
