from pathlib import Path
p=Path(__file__).resolve().parent/'runtime/web/services/distributions.py';s=p.read_text()
s=s.replace('    profiles = accounts()\n', '    placeholders = ",".join("?" for _ in ids)\n    parameters = tuple(ids)\n    profiles = accounts()\n',1)
s=s.replace('conn.execute("SELECT * FROM publications ORDER BY created_at DESC") if r["script_id"] in ids', 'conn.execute(f"SELECT * FROM publications WHERE script_id IN ({placeholders}) ORDER BY created_at DESC", parameters)')
s=s.replace('conn.execute("SELECT script_id,version,platform,account_uid,render_id,updated_at FROM distribution_drafts") if r["script_id"] in ids','conn.execute(f"SELECT script_id,version,platform,account_uid,render_id,updated_at FROM distribution_drafts WHERE script_id IN ({placeholders})", parameters)')
s=s.replace('conn.execute("SELECT id,uid,script_id,version,render_id,status,error,bvid,updated_at FROM uploads ORDER BY created_at DESC")', 'conn.execute(f"SELECT id,uid,script_id,version,render_id,status,error,bvid,updated_at FROM uploads WHERE script_id IN ({placeholders}) ORDER BY created_at DESC", parameters)')
s=s.replace('conn.execute("SELECT id,uid,script_id,version,render_id,status,error,work_id,phase,updated_at FROM jobs WHERE kind=\'UPLOAD\' ORDER BY created_at DESC")','conn.execute(f"SELECT id,uid,script_id,version,render_id,status,error,work_id,phase,updated_at FROM jobs WHERE kind=\'UPLOAD\' AND script_id IN ({placeholders}) ORDER BY created_at DESC", parameters)')
s=s.replace('''        for task in social_publishing.store(platform).list(kind="UPLOAD"):
            if task['script_id'] in ids:
                tasks.append({**task,"platform":platform})''','''        with social_publishing.store(platform).connect() as conn:
            for task in conn.execute(f"SELECT id,uid,script_id,version,render_id,status,error,work_id,updated_at FROM jobs WHERE kind='UPLOAD' AND script_id IN ({placeholders}) ORDER BY created_at DESC", parameters):
                tasks.append({**dict(task), "platform": platform})''')
s=s.replace('conn.execute("SELECT DISTINCT script_id,version FROM renders") if r["script_id"] in ids','conn.execute(f"SELECT DISTINCT script_id,version FROM renders WHERE script_id IN ({placeholders})", parameters)')
p.write_text(s)
