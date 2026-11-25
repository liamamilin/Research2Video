# 复验方法

从 `autovideo/` 执行。需要项目现有 Node/Remotion/ELK、macOS Hiragino 字体，以及真实语音验收使用的本地 VoxCPM/Qwen 运行时。所有输出目录必须使用新名字，脚本不会覆盖历史结果。不要重新生成已有语料。

```sh
NODE_PATH=g0/vendor/remotion-audiogram/node_modules \
  g0/vendor/remotion-audiogram/node_modules/.bin/esbuild \
  g3/acceptance/generality-20261111/probe.ts --bundle --platform=node --format=cjs \
  --outfile=g3/data/validation/generality-review-probe.cjs

python3 g3/acceptance/generality-20261111/measure.py --current --split holdout2 \
  --bundle g3/data/validation/generality-review-probe.cjs \
  --output g3/data/validation/generality-review-measurement

python3 g3/acceptance/generality-20261111/render_gallery.py --split holdout2 \
  --measurement g3/data/validation/generality-review-measurement \
  --output g3/data/validation/generality-review-video

python3 g3/acceptance/generality-20261111/media_qa.py \
  g3/data/validation/generality-review-video

python3 g3/acceptance/generality-20261111/speech_probe.py --split holdout2 \
  --output g3/data/validation/generality-review-speech

python3 g3/acceptance/generality-20261111/speed_probe.py \
  --source g3/data/validation/generality-review-speech \
  --output g3/data/validation/generality-review-speed --speed 1.5

python3 -m pytest tests g4/tests g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q
```

`development` 用于调试；`holdout` 已经降为回归组；`holdout2` 是本轮最终留出结果。今后如果根据任何已知实例修改规则，这些样例只能用作回归，不应再次声称它们是独立留出。使用 `corpus.build(new_name, new_seed)` 生成并记录新批次，冻结实现后再读取实例。

`measure.py` 检查输出几何、对象覆盖、字符位置和字体测量，不以非零失败数中止：本轮保留的容量失败是需要查看的结果。检查 `results.json`，不能把进程退出码零当作所有画面通过。

`render_gallery.py` 的流程/素材标题触发缺少 `scene reveal`，已在 REPORT 中列为夹具缺口；该视频只承担结构画面的验收，另有标题接入短片。不要把合成几何样片当作真实语音验收。首次基线使用的渲染器源码快照位于运行产物 `baseline/renderer-source`，`baseline-probe.ts` 需该快照；最终版本复验不依赖它。
