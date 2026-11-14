# 开头要点卡：真实出片验证（2026-11-04）

用 A/B 预览路径（`core/experiments/preview.py`，不写出片表、不发布）对一条最近的自动视频出了 B 版：
来源出片 `8fee1f88e3ca4f2e817e52f2e6ce8381`（《同样200页招股书：Kimi能指到第几页第几段……》）。

- 出片成功：`video.mp4` 1920×1080、30fps、2:32.20，ffmpeg 全片解码通过（exit 0），AutoVideo package.json `full_decode: PASS`。
- 要点卡文字生成成功：`{"big": "免费收50份", "figure": "200页 vs 50份"}`。
- **但要点卡没有出现在成片里**：`a-first-frame.png`（A）和 `b-first-frame.png`（B）开头一样。
  `packaging-resolved.json` 的 selection 里 `intro.enabled: false`：视频带品牌（LINTHESIS），
  AutoVideo `g3/brand_profiles.py` 会把 intro / outro / badge / sound 一律关掉。

结论：`presentation.opening_card` 在开关登记表里标为「待确认」，不能用于实验，直到决定是否改 AutoVideo 的品牌规则。
