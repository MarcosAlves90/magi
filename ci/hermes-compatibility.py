"""Read-only capability check of Hermes APIs; mutations use only a temporary profile."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory


def check():
    # Override profile before importing Hermes; never touch the user's profile.
    with TemporaryDirectory(prefix="magi-hermes-compat-") as profile:
        os.environ["HERMES_HOME"] = profile
        from hermes_constants import get_hermes_home
        from tools.memory_tool import apply_memory_pending, load_on_disk_store
        from tools.memory_tool_store import MemoryStore

        resolved = Path(get_hermes_home()).resolve()
        if resolved != Path(profile).resolve():
            raise RuntimeError(f"Hermes profile isolation did not take effect: {resolved}")
        if not callable(apply_memory_pending) or not callable(load_on_disk_store):
            raise RuntimeError("Required Hermes memory public callables are unavailable")
        for target in ("memory", "user"):
            store = MemoryStore(memory_char_limit=2000, user_char_limit=2000)
            initial = f"Magi compatibility {target} original"
            updated = f"Magi compatibility {target} updated"
            added = store.add(target, initial)
            if not added.get("success"):
                raise RuntimeError(f"Hermes {target} add failed: {added}")
            outcome = store.apply_batch(target, [
                {"action": "remove", "old_text": initial, "matched_entry": initial},
                {"action": "add", "content": updated},
            ])
            if not outcome.get("success"):
                raise RuntimeError(f"Hermes {target} apply_batch failed: {outcome}")
            reloaded = MemoryStore(memory_char_limit=2000, user_char_limit=2000)
            reloaded.load_from_disk()
            entries = reloaded.memory_entries if target == "memory" else reloaded.user_entries
            if initial in entries or updated not in entries:
                raise RuntimeError(f"Hermes {target} persistence mismatch")
            print(f"Hermes {target}: public add + pinned atomic apply_batch + reload OK")


if __name__ == "__main__":
    check()
