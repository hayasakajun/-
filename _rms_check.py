from pathlib import Path
import time

def wait_downloads(folder, timeout=30):
    deadline = time.monotonic() + timeout
    previous = None
    same = 0
    while time.monotonic() < deadline:
        try:
            paths = list(folder.iterdir())
            pending = any(p.suffix.lower() in ('.crdownload', '.tmp', '.part') for p in paths)
            files = [p for p in paths if p.is_file() and p.suffix.lower() in ('.csv', '.zip', '.xlsx', '.xls') and p.stat().st_size > 0]
            current = tuple(sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in files))
        except FileNotFoundError:
            previous, same = None, 0
            time.sleep(1)
            continue
        same = same + 1 if current == previous else 0
        if files and not pending and same >= 2:
            return files
        previous = current
        time.sleep(1)
    return []

