from pathlib import Path
p=Path('runtime/web/services/video_render.py');s=p.read_text().replace('import re\n','import re\nimport sqlite3\n');s=s.replace('''        try:
            process(render_id, client=client)
        finally:
''','''        try:
            while True:
                try:
                    process(render_id, client=client)
                    break
                except sqlite3.OperationalError as exc:
                    if not _database_busy(exc):raise
                    logger.warning('Render %s waits for local database; preserving the existing remote task',render_id)
                    time.sleep(2)
        finally:
''');s=s.replace('''            except Exception as exc:  # noqa: BLE001 - a crashed render must not look in-flight
                logger.exception''','''            except Exception as exc:  # noqa: BLE001 - a crashed render must not look in-flight
                if _database_busy(exc):raise
                logger.exception''');needle='def process(render_id:';i=s.index(needle);s=s[:i]+'''def _database_busy(exc: Exception) -> bool:
    return isinstance(exc,sqlite3.OperationalError) and any(word in str(exc).lower() for word in ('locked','busy'))


'''+s[i:];p.write_text(s)
