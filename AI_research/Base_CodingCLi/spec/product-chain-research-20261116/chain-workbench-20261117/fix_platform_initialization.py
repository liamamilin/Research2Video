from pathlib import Path
root=Path(__file__).resolve().parent/'runtime'
p=root/'core/sqlite_util.py';s=p.read_text().replace('import sqlite3\n','import sqlite3\nimport threading\n',1).replace('from typing import Iterator, Optional','from typing import Callable, Iterator, Optional')
s+='''\n\n_SCHEMA_IDENTITIES: dict[str, tuple[int, int]] = {}
_SCHEMA_LOCK = threading.RLock()


def initialize_schema_once(path: str, initialize: Callable[[], None]) -> None:
    """Ensure a schema once per physical database, including replacement recovery."""
    def identity():
        try:
            stat = os.stat(path)
            return stat.st_dev, stat.st_ino
        except FileNotFoundError:
            return None

    with _SCHEMA_LOCK:
        current = identity()
        if current is not None and _SCHEMA_IDENTITIES.get(path) == current:
            return
        initialize()
        current = identity()
        if current is not None:
            _SCHEMA_IDENTITIES[path] = current
''';p.write_text(s)
for name in ('douyin_store.py','platform_store.py'):
 p=root/'core'/name;s=p.read_text();needle='        with self.connect('
 position=s.index(needle,s.index('    def __init__'))
 s=s[:position]+'        sqlite_util.initialize_schema_once(self.path, self._initialize_schema)\n\n    def _initialize_schema(self):\n'+s[position:];p.write_text(s)
p=Path(__file__).resolve().parent/'app.py';s=p.read_text();s=s.replace('DB_ANCHORS=[sqlite3.connect(str(ROOT/"state"/name),check_same_thread=False) for name in ("video.db","users.db","reports.db","schedules.db","chains.db")]', 'DB_ANCHOR_PATHS={ROOT/"state"/name for name in ("video.db","users.db","reports.db","schedules.db","chains.db","platforms.db","douyin.db")}\nDB_ANCHOR_PATHS.update((ROOT/"state/platforms").glob("*/queue.db"))\nDB_ANCHORS=[sqlite3.connect(str(path),check_same_thread=False) for path in sorted(DB_ANCHOR_PATHS)]');p.write_text(s)
