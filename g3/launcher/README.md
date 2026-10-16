# AutoVideo 本机启动器

已安装：`~/Applications/AutoVideo.app`，并固定到程序坞。点击绿色 AutoVideo 图标即可启动或打开网页。

- 服务尚未运行：使用已配置的本机 Python、Node 和当前项目目录，启动 `127.0.0.1:8011`，等待可用后打开默认浏览器。
- 服务已经运行：直接打开页面，复用同一制作队列。
- 端口被其他应用占用：显示错误，不终止或替换其他服务。
- 启动器打开页面后退出。后台服务独立运行，关闭浏览器不会取消制作任务。

这是当前本机项目的轻量原生快捷启动器，不内置 Python、Node、模型或完整安装环境。当前构建约 0.56 MB；验证环境为本机 Apple Silicon / macOS 26.6.2。

## 重建与安装

在当前项目根目录，使用已配置应用依赖的 Python 运行：

```sh
python3 g3/launcher/build.py --install --pin-dock
```

需要本机已有 Swift 命令行编译工具。构建时检测 Python 依赖和 Node，将当前项目及运行时路径写入启动器；不写入 DeepSeek 密钥。移动项目或更换运行时路径后，重新构建即可。

构建产物在忽略的 `g3/data/launcher/build/`。安装仅替换同一启动器标识的应用，并保留旧版备份；不会覆盖同名的其他应用。固定程序坞前保存原 Dock 配置，只增加本应用。

## 日志与验证

- `g3/data/launcher/server.log`：由启动器启动的后台服务日志。
- `g3/data/launcher/launcher.log`：启动或复用记录。
- `g3/data/launcher/server.json`：由启动器启动的服务 PID，仅作诊断。
- `g3/data/launcher/verification.json`：本机冷启动、复用、环境、任务数量与 Dock 固定的实际验证。

已从实际安装的 `.app` 验证冷启动和二次启动：服务独立运行、单一监听进程、生成依赖就绪，原有 33 条任务未改变。新增回归覆盖已有服务复用，以及其他应用占用端口时的保护行为。
