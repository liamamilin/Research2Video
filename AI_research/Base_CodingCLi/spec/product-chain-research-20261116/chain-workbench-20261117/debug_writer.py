import app,logging,traceback
from web.services import video_workflow
logging.basicConfig(level=logging.WARNING,force=True)
try:video_workflow.run('9541c940e2004129ab0a4288edfd822d',started_by='isolation-debug')
except Exception:traceback.print_exc()
