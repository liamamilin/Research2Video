# 复现

从 `autovideo/` 执行。已有语料与证据目录不可覆盖；每次使用新的输出目录。不要重新执行 `corpus.py` 改写已冻结语料。

```sh
NODE_PATH="$PWD/g0/vendor/remotion-audiogram/node_modules" ./g0/vendor/remotion-audiogram/node_modules/.bin/esbuild g3/acceptance/relation-layout-20261111/probe.ts --bundle --platform=node --outfile=/tmp/relation-probe.cjs
python3 g3/acceptance/relation-layout-20261111/measure.py --split holdout --bundle /tmp/relation-probe.cjs --output g3/data/validation/relation-recheck
python3 g3/acceptance/relation-layout-20261111/render.py --measurement g3/data/validation/relation-recheck --output g3/data/validation/relation-recheck-native
python3 g3/acceptance/relation-layout-20261111/render.py --measurement g3/data/validation/relation-recheck --output g3/data/validation/relation-recheck-hybrid --hybrid
python3 g3/acceptance/relation-layout-20261111/media_qa.py g3/data/validation/relation-recheck-native
python3 g3/acceptance/relation-layout-20261111/media_qa.py g3/data/validation/relation-recheck-hybrid
python3 -m pytest tests g4/tests g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q
```

真实语音验证使用已配置的本地 VoxCPM、Qwen 强制对齐器及 Chrome；不调用脚本规划模型：

```sh
python3 g3/acceptance/relation-layout-20261111/speech.py --output g3/data/validation/relation-speech-recheck
python3 g3/acceptance/relation-layout-20261111/speech_speed.py --source g3/data/validation/relation-speech-recheck --output g3/data/validation/relation-speed-recheck --speed 1.5
python3 g3/acceptance/relation-layout-20261111/speech_hybrid.py --source g3/data/validation/relation-speech-recheck --output g3/data/validation/relation-hybrid-speech-recheck
python3 g3/acceptance/relation-layout-20261111/media_qa.py g3/data/validation/relation-hybrid-speech-recheck --spoken
```

`results/` 保存紧凑测量结果；`manifest.json`、`implementation-freeze.json` 和 `freeze-amendment.json` 记录语料/实现冻结与仅标题的接入修复。`experiment.py` 是已放弃路线 A 的有限开发实验，不属于生产路径。

测量脚本支持 `--cases` 输入独立回归语料；该输入会记录自身哈希，但不冒充冻结留出集。原先 20 例转换输入保存在本轮运行目录 `previous-flow-cases.json`。
