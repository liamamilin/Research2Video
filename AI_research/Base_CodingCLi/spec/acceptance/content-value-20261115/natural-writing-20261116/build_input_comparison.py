from pathlib import Path
from html import escape
import json
root=Path(__file__).resolve().parents[4]
h=root/'spec/acceptance/content-value-20261115/natural-writing-20261116'
b=root/'state/acceptance-content-value-20261115/natural-writing-20261116'
sets=[('科学：同一选题、相同作者',[("追加补材（对照）",'input-dossier-r1/science/control'),('旧稿整合 R1','input-dossier-r1/science/candidate'),('旧稿整合 R2','input-dossier-r2/science/candidate'),('选题与来源再组织','source-dossier-r1/science/candidate'),('来源正文直接交作者','direct-sources-r1/science/candidate')]),('文化：同一选题、相同作者',[("追加补材（对照）",'input-dossier-r1/culture/control'),('旧稿整合 R1','input-dossier-r1/culture/candidate'),('旧稿整合 R2','input-dossier-r2/culture/candidate'),('选题与来源再组织','source-dossier-r1/culture/candidate'),('来源正文直接交作者','direct-sources-r1/culture/candidate')]),('软件：仅改变传入原文范围',[('全部原文','soft-final-entry/software/candidate'),('研究所引完整来源','input-focus-r1/software/candidate')]),('科学周报：仅改变传入原文范围',[('全部原文','input-focus-r1/science/control'),('研究所引完整来源','input-focus-r1/science/candidate')])]
for domain, (_, variants) in zip(['science', 'culture'], sets[:2]):
 variants.extend([
   ('补取主问题来源，直接交作者', f'topic-sources-r1/{domain}/candidate'),
   ('新来源经研究整理', f'topic-dossier-r1/{domain}/candidate'),
   ('新来源经现行追加补材', f'topic-enrich-r1/{domain}/candidate'),
   ('只移除继承原文风格', f'input-style-free-r1/{domain}/candidate')])
sets.extend([
 ('未调试的产业日报', [('软化前', 'soft-late-holdout-control-r2/industry_daily/control'), ('最终软化版', 'soft-late-holdout/industry_daily/candidate')]),
 ('原反馈：SVG', [('原稿', 'original-feedback-final-r2/svg/baseline'), ('当前版本', 'original-feedback-final-r2/svg/candidate')]),
 ('原反馈：Code Mode', [('原稿', 'original-feedback-final-r2/code/baseline'), ('当前版本', 'original-feedback-final-r2/code/candidate')])])
sets.extend([
 ('地理插画：同一选题参考，不同自动选题', [('原研究提示', 'research-brief/geography/control'), ('省去选题评估要求', 'research-brief/geography/candidate')]),
 ('历史插画：两版均选古代计时', [('原研究提示', 'research-brief/history/control'), ('省去选题评估要求', 'research-brief/history/candidate')])])
sets.extend([
 ('科学周报：同研究、独立开场和收尾可选', [('原章节提示', 'optional-roles/science/control'), ('可选章节角色', 'optional-roles/science/candidate')]),
 ('文化周报：同研究、独立开场和收尾可选', [('原章节提示', 'optional-roles/culture/control'), ('可选章节角色', 'optional-roles/culture/candidate')])])
sets.extend([
 ('地理插画：同初稿与同来源，局部放置补材', [('已有追加材料', 'research-brief/geography/candidate'), ('放到相关段落旁', 'placed-supplement/geography/candidate')]),
 ('历史插画：同初稿与同来源，局部放置补材', [('已有追加材料', 'research-brief/history/candidate'), ('放到相关段落旁', 'placed-supplement/history/candidate')]),
 ('T03：同一报告，放开教程首尾', [('原提示', 'tutorial-wrap/t03/control'), ('软建议版', 'tutorial-wrap/t03/candidate')]),
 ('快讯合集：同一研究，放开条目首尾', [('原提示', 'flash-daily-wrap/flash_daily/control'), ('软建议版', 'flash-daily-wrap/flash_daily/candidate')]),
 ('T05：同一既定预告，选材输出有差异', [('原提示', 'sequel-wrap/t05/control'), ('自然预告版', 'sequel-wrap/t05/candidate')])])
sets.extend([
 ('T05 旧报告：先恢复选题和横评资料', [('原分包稿', 'sequel-wrap/t05/candidate'), ('保留比较条目', 't05-kept-item/legacy/candidate')]),
 ('T05 现行报告：同题文风比较', [('有选题标记的实际稿件', 't05-kept-item/marked/candidate')])])
sets.extend([
 ('SVG 计费：同原文与事件，只删重复摘要', [('原请求', 'original-feedback-final-r2/svg/candidate'), ('单份事件摘要', 'author-input-dedup/svg/candidate')]),
 ('T05 文案比较：同原文与事件，只删重复摘要', [('原请求', 't05-kept-item/marked/candidate'), ('单份事件摘要', 'author-input-dedup/marked_t05/candidate')])])
flash_variants = [
 ('原研究提示与选题', 'flash-live/same-supply-control'),
 ('按需填写研究备注', 'flash-live/same-supply-current'),
 ('共享选题备注及结构可选', 'flash-live/same-supply-soft-selector'),
 ('共享选题可接受本题所需来源', 'flash-live/same-supply-full-sources'),
]
flash_completed = [(label, rel) for label, rel in flash_variants if (b / rel / 'script.txt').is_file()]
if flash_completed:
 sets.append(('快讯：同一批自动采集材料，独立选题与补材', flash_completed))
sets.append(('单条快讯：同一事故与原文，调整读全文时的选题交接', [
 ('沿用初选的承诺与解释', 'flash-live/selection-replay-control'),
 ('读全文后组织解释', 'flash-live/selection-replay-candidate')]))
if (b / 'flash-live/selection-replay-warm/script.txt').is_file():
 sets.append(('日常采集自动选出的新题', [
  ('当前读原文与单条入口，问题见审读记录', 'flash-live/selection-replay-warm')]))
parts=['''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>输入优先：完整研究与文稿对照</title><style>body{font:16px/1.8 system-ui;margin:32px auto;padding:0 24px;max-width:1700px;color:#202632;background:#f5f6f8}h1{font-size:26px}h2{font-size:22px}nav a{margin-right:20px}summary{cursor:pointer;font-weight:600;padding:12px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px}article{background:white;border:1px solid #d8dce3;padding:20px;min-width:0}pre{white-space:pre-wrap;font:15px/1.9 system-ui;overflow-wrap:anywhere}.note{background:#fff1d6;padding:14px;border-left:4px solid #b67700}code{font-size:12px;overflow-wrap:anywhere}</style><h1>输入优先：完整研究与文稿对照</h1><p class="note">隔离试验；没有发布。各研究输入对照固定作者；章节试验只调整角色措辞。减少输入、章节或篇幅不单独算质量提升。生成文本包含尚未解决的问题，实验稿不代表已采用的方案。运行记录与采用决定见 INPUT-FOCUS-REVIEW.txt。</p>''']
for i,(title, variants) in enumerate(sets):
 parts.append(f'<section><h2>{escape(title)}</h2><div class="grid">')
 for label, rel in variants:
  folder=b/rel
  parts.append(f'<article><h3>{escape(label)}</h3><code>{escape(rel)}</code>')
  for name,file in [('完整文稿','script.txt'),('研究底稿','research.txt')]:
   path=folder/file
   if path.exists():parts.append(f'<details {"open" if file=="script.txt" else ""}><summary>{name}</summary><pre>{escape(path.read_text())}</pre></details>')
  if not (folder/'script.txt').exists():parts.append('<p>此稿尚未完成。</p>')
  parts.append('</article>')
 parts.append('</div></section>')
parts.append('</html>')
(h/'input-comparison.html').write_text(''.join(parts))
