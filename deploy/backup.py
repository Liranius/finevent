"""SQLite online backup, including WAL; run as the finevent service account."""

import datetime
from contextlib import closing
import os
from pathlib import Path
import sqlite3


def backup(data_dir: Path) -> Path:
    os.umask(0o077)
    destination = data_dir / "backups"
    destination.mkdir(exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = destination / f"finevent-{stamp}.db"
    with closing(sqlite3.connect(f"{(data_dir / 'finevent.db').as_uri()}?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(target)) as output:
            source.backup(output)
            if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
    for old in sorted(destination.glob("finevent-????????T??????Z.db"))[:-7]:
        old.unlink()
    return target


if __name__ == "__main__":
    print(backup(Path("/srv/example/finevent-data")))
