"""
Interactive category editor: add, edit, or remove entries in config.json's
"categories" section without hand-editing the file.

Only ever touches the "categories" key - "_comment", "exclude",
"admin_pin", and anything else already in the file is read back and
written out untouched. Built-in categories (from DEFAULT_CATEGORY_MAP)
that haven't been customized aren't written to config.json at all, so a
later change to the app's defaults still takes effect for anyone who
hasn't explicitly overridden that category.
"""

from pathlib import Path

from config import DEFAULT_CATEGORY_MAP, load_raw_config, save_raw_config


def normalize_extensions(raw: str) -> list:
    """'.jpg, png ,.GIF, ' -> ['.jpg', '.png', '.gif'] - accepts entries
    with or without a leading dot, any case, comma separated, trims blanks
    and drops duplicates while preserving order."""
    seen = set()
    exts = []
    for part in raw.split(","):
        part = part.strip().lower()
        if not part:
            continue
        ext = part if part.startswith(".") else f".{part}"
        if ext not in seen:
            seen.add(ext)
            exts.append(ext)
    return exts


def print_categories(category_map: dict, overridden: set):
    print("\n  Current categories:")
    print("  " + "-" * 44)
    for name in sorted(category_map):
        tag = " (custom)" if name in overridden else ""
        print(f"    {name}{tag}: {', '.join(category_map[name])}")


def add_or_edit_category(category_map: dict, overridden: set):
    name = input("\n  Category name (new, or an existing one to edit): ").strip()
    if not name:
        print("  Cancelled - no name entered.")
        return

    existing = category_map.get(name)
    if existing:
        print(f"  Current extensions for '{name}': {', '.join(existing)}")

    raw = input(
        "  Enter the FULL list of extensions for this category, comma separated\n"
        "  (e.g. .jpg, .png, .gif) - this replaces the list above entirely: "
    ).strip()
    exts = normalize_extensions(raw)
    if not exts:
        print("  No valid extensions entered - nothing changed.")
        return

    category_map[name] = exts
    overridden.add(name)
    print(f"  '{name}' now maps: {', '.join(exts)}")


def remove_category_override(category_map: dict, overridden: set):
    if not overridden:
        print("\n  Nothing to remove - every category here is still a built-in default.")
        return

    names = sorted(overridden)
    print("\n  Which one do you want to remove? (built-ins revert to default, "
          "fully custom categories are deleted)")
    for i, n in enumerate(names, start=1):
        print(f"    {i}. {n}")
    raw = input("  Enter a number, or press Enter to cancel: ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(names)):
        print("  Cancelled.")
        return

    name = names[int(raw) - 1]
    overridden.discard(name)
    if name in DEFAULT_CATEGORY_MAP:
        category_map[name] = list(DEFAULT_CATEGORY_MAP[name])
        print(f"  '{name}' reverted to its built-in default: {', '.join(category_map[name])}")
    else:
        del category_map[name]
        print(f"  '{name}' removed.")


def edit_categories(config_path: Path, category_map: dict) -> dict:
    """
    Interactive loop for editing categories. Works on a COPY of
    category_map, so nothing takes effect until the user saves. Returns
    the map the caller should use from now on - the updated one if saved,
    or the original if discarded.
    """
    working = {k: list(v) for k, v in category_map.items()}
    raw_config = load_raw_config(config_path)
    overridden = set(raw_config.get("categories", {}).keys())

    while True:
        print("\n" + "=" * 50)
        print("  MANAGE CATEGORIES")
        print("=" * 50)
        print_categories(working, overridden)
        print("\n  1. Add or edit a category")
        print("  2. Remove a custom category / override")
        print("  3. Save and return to menu")
        print("  4. Discard changes and return to menu")
        choice = input("  Enter 1-4: ").strip()

        if choice == "1":
            add_or_edit_category(working, overridden)
        elif choice == "2":
            remove_category_override(working, overridden)
        elif choice == "3":
            raw_config["categories"] = {name: working[name] for name in overridden if name in working}
            save_raw_config(config_path, raw_config)
            print(f"\n  Saved to {config_path}")
            return working
        elif choice == "4":
            print("\n  Changes discarded.")
            return category_map
        else:
            print("  Invalid choice - enter a number from 1 to 4.")