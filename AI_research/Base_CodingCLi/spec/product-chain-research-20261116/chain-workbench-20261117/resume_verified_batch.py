import app,json
from web.services import chain_runner
row=chain_runner.retry('604580ff078347c6815d2468068ed4e1',{'username':'isolation-admin','role':'admin'})
(app.ROOT/'evidence/voice-dependency-resume.json').write_text(json.dumps({'id':row['id'],'status':row['status'],'attempt':row['attempt'],'note':'隔离渲染服务补齐生产音色的已有本地依赖，复用原文稿继续'},ensure_ascii=False,indent=2))
print(row['id'],row['status'])
