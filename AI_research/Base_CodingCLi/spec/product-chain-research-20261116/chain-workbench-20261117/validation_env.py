"""Disposable second-level isolation so tests never rewrite the live workbench."""
from pathlib import Path
import shutil,sqlite3,tempfile

def setup(app_module,copy_video=False):
 source=app_module.ROOT
 (source/'validation').mkdir(exist_ok=True)
 target=Path(tempfile.mkdtemp(prefix='management-',dir=source/'validation'))
 external=Path(tempfile.mkdtemp(prefix='validation-',dir=source/'tmp'))
 (external/'state').mkdir();(target/'state').symlink_to(external/'state',target_is_directory=True)
 for folder in ('config','jobs'):
  shutil.copytree(source/folder,target/folder)
 for folder in ('logs','output'):(external/folder).mkdir();(target/folder).symlink_to(external/folder,target_is_directory=True)
 for name in (('video.db',) if copy_video else ())+('users.db','schedules.db'):
  with sqlite3.connect(source/'state'/name) as src,sqlite3.connect(target/'state'/name) as dst:src.backup(dst)
 for name in ('video.db','users.db','schedules.db','chains.db'):
  anchor=sqlite3.connect(target/'state'/name,check_same_thread=False);anchor.execute('PRAGMA journal_mode=WAL');anchor.execute('SELECT name FROM sqlite_master LIMIT 1').fetchall();app_module.DB_ANCHORS.append(anchor)
 from web.settings import get_settings
 settings=get_settings()
 for name in ('config','jobs','state','logs','output'):setattr(settings.paths,name+'_dir',str(target/name))
 from core import state,activity,lock
 from core.video import store
 from web.auth import db as users
 state.use_state_dir(str(target/'state'));activity.use_state_dir(str(target/'state'));lock._LOCK_DIR=str(target/'state/locks')
 store.use_state_dir(str(target/'state'));store.init_db();users._DB_PATH_OVERRIDE=str(target/'state/users.db')
 return target
