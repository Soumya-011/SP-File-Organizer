"""
Generic, reusable interactive prompts - the ones not tied to any single
feature's data structures. Feature-specific menus (which categories to
organize, which files to rename, etc.) live next to that feature's own
module instead, since they need intimate knowledge of that feature's data.
"""

import getpass
import hashlib


def select_multiple(items: list, labels: list, header: str = None,
                     prompt: str = "Enter numbers separated by commas, or 0 for all: ",
                     indent: str = "  ") -> list:
    """
    Generic numbered multi-select prompt, shared by every menu in this app
    that lets you pick several items by number (or 0 for all of them).
    `items` and `labels` must be parallel lists - `labels[i]` is what gets
    printed next to `items[i]`'s number. Returns the chosen subset of
    `items`, preserving their original order.
    """
    if not items:
        return []

    if header:
        print(header)
    for i, label in enumerate(labels, start=1):
        print(f"{indent}{i}. {label}")
    print(f"{indent}0. All of the above")

    raw = input(prompt).strip()
    if raw == "0":
        return items

    chosen = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(items):
            chosen.append(items[int(part) - 1])
    return chosen


def confirm_dry_run_then_execute(execute_fn, confirm_msg: str, cancel_msg: str,
                                  force_dry_run_first: bool = None,
                                  dry_run_prompt: str = "Preview this as a dry run first? (y/n): ",
                                  apply_prompt: str = "\nApply these changes for real now? (y/n): ",
                                  no_change_msg: str = "No changes were made."):
    """
    The "preview as a dry run first?" / "apply for real now?" flow used
    everywhere in this app that's about to change files on disk (bulk
    rename, duplicate cleanup, mismatch fixing, and the main organize run).

    `execute_fn(dry_run)` performs (or previews) the action and returns
    whatever log entries resulted. `force_dry_run_first`, if not None,
    skips the initial question and goes straight into preview mode (used
    for the --dry-run CLI flag) - otherwise the user is asked.

    Returns the log entries from the REAL run, or None if the user backed
    out (declined to apply after previewing, or declined the direct
    confirmation).
    """
    dry_run_first = (
        force_dry_run_first if force_dry_run_first is not None
        else input(dry_run_prompt).strip().lower() == "y"
    )

    if dry_run_first:
        execute_fn(True)
        apply_now = input(apply_prompt).strip().lower()
        if apply_now != "y":
            print(no_change_msg)
            return None
    else:
        confirm = input(confirm_msg).strip().lower()
        if confirm != "y":
            print(cancel_msg)
            return None

    return execute_fn(False)


def verify_admin_access(admin_pin) -> bool:
    """
    Prompts the user for the admin PIN (hidden input) and securely verifies it.
    """
    if not admin_pin:
        print("\n  No admin PIN configured in config.json. Action denied.")
        return False

    attempt = getpass.getpass("  Enter Admin PIN: ").strip()
    attempt_hash = hashlib.sha256(attempt.encode('utf-8')).hexdigest()
    
    if attempt_hash == str(admin_pin):
        return True
        
    print("  Incorrect PIN.")
    return False


def ask_positive_number(prompt: str) -> float:
    while True:
        raw = input(prompt).strip()
        try:
            value = float(raw)
            if value < 0:
                raise ValueError
            return value
        except ValueError:
            print("  Please enter a positive number.")


def ask_separation_rules():
    """Ask whether the user wants large/old-file separation, and how."""
    print("\n" + "-" * 40)
    choice = input("Also separate large and/or old files? (y/n): ").strip().lower()
    if choice != "y":
        return None

    print("\nWhen should this happen?")
    print("  1. Before organizing by type")
    print("     -> whole-folder 'Large Files' / 'General Old' folders")
    print("  2. After organizing by type")
    print("     -> 'Large <Category>' / 'Old <Category>' folders inside chosen category folders")
    timing = "after" if input("Enter 1 or 2: ").strip() == "2" else "before"

    print("\nWhich rule(s)?")
    print("  1. Size (large files)")
    print("  2. Age (old files)")
    print("  3. Both")
    rule_raw = input("Enter 1, 2, or 3: ").strip()
    want_size = rule_raw in ("1", "3")
    want_age = rule_raw in ("2", "3")

    if not want_size and not want_age:
        print("  No valid rule selected - skipping size/age separation.")
        return None

    size_mb = None
    age_days = None
    if want_size:
        size_mb = ask_positive_number("  Minimum size in MB to count as 'large' (e.g. 100): ")
    if want_age:
        age_days = ask_positive_number("  Minimum age in days to count as 'old' (e.g. 180): ")

    return {"timing": timing, "want_size": want_size, "want_age": want_age,
            "size_mb": size_mb, "age_days": age_days}


def ask_continue_after_pre() -> bool:
    """Returns True if the user wants to STOP here (skip type-organizing)."""
    print("\nWhat would you like to do next?")
    print("  1. Continue - also organize the remaining files by type")
    print("  2. Stop here - only apply this size/age separation")
    choice = input("Enter 1 or 2: ").strip()
    return choice == "2"