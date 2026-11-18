from pathlib import Path
import os,sys
HERE=Path(__file__).resolve().parent
ROOT=HERE/'runtime/renderer'
SOURCE=HERE.parents[4]/'autovideo'
os.chdir(ROOT);sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
os.environ['AUTOVIDEO_DATA_DIR']=str(ROOT/'g3/data')
os.environ['PYTHONDONTWRITEBYTECODE']='1';os.environ['TMPDIR']=str(HERE/'runtime/tmp')
os.environ['XDG_CACHE_HOME']=str(HERE/'runtime/cache')
from dotenv import dotenv_values
for k,v in dotenv_values(SOURCE/'.env').items():
    if v is not None:os.environ[k]=v
os.environ['AUTOVIDEO_DATA_DIR']=str(ROOT/'g3/data')
from g3.app import app
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=9253,access_log=False)
