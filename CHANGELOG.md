# Changelog

本文件记录 FNE-py 的重要变更。版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-09-12

首个 Python 移植版本，功能对齐上游 Go 版 FNE。

### 新增

- **NCM（网易云）解密** —— AES-ECB 解出密钥与元数据，256 字节周期密钥流异或，
  输出 FLAC / MP3 并写入歌名、歌手、专辑与内嵌封面
- **QMC2（QQ 音乐）解密** —— `.mflac` / `.mflac0` / `.mflach` / `.mgg` / `.mgg0` /
  `.mgg1` / `.mggl`，支持 Map（短密钥）与分段 RC4（长密钥）两种密码
- **`authst` 令牌提取** —— 同时识别 JSON（紧凑/带空格）、XML、query、长度前缀二进制
  四种布局，ASCII 与 UTF-16LE 双编码，并按可信度打分
- **`authst` 令牌缓存** —— 读取过的令牌全部记录到 `.fne/authst_cache.json`，
  下次优先复用（0.5 ms，无需扫描进程内存）；令牌失效时自动顺延到下一个候选
- **增量转换** —— 输出目录中已存在的同名文件自动跳过，可随时中断重跑
- **多线程转换** —— 默认 8 个 worker，API 请求带并发与随机延迟限流
- **CLI** —— `-i` / `-o` / `-r` / `-j` / `--verbose` / `-V`，支持 `config.json`，
  无参数时弹出目录选择框
- **图形界面 `fne-gui`** —— tkinter/ttk 实现，零额外依赖：目录选择、递归开关、
  并行数调节、实时进度条与日志、可中途停止（已转好的文件保留）
- **CI（GitHub Actions）** —— `pyflakes` 静态检查、Windows 上跨 Python 3.9/3.13
  跑测试、构建 sdist + wheel 并校验 wheel 可装可跑；打 `v*` tag 时自动创建
  GitHub Release（会先校验 tag 与 `__version__` 一致）
- **`tools/compare_audio.py`** —— 比对两批 FLAC 的音频帧，发布前手工回归用

### 变更

- **两个前端共用一个管线** —— `cli.run()` 不再直接打印结果，改为通过
  `on_log` / `on_progress` 回调汇报并返回 `Summary` 对象；CLI 与 GUI 各自决定怎么呈现
- **项目结构收敛** —— 文档入 `docs/`，工具入 `tools/`，根目录只留工程文件
- **依赖统一由 `pyproject.toml` 声明** —— 移除 `requirements.txt` /
  `requirements-dev.txt`（用 `pip install -e ".[dev]"` 装开发依赖）
- 以 Python 版为唯一实现，移除 Go 版本及其 fork 关联

### 移除

- Go 实现与 fork 仓库关联
- 一次性验证脚本（`_verify_ncm.py` / `_compare.py` / `_check_cache.py`）与
  转换产物目录

### 可靠性

- 输出文件**原子写入**（临时文件 + `os.replace`），中断或磁盘满不会留下半截文件
- 增量判断忽略 0 字节残留文件
- NCM 输入校验：块长度、元数据包装前缀、**`format` 白名单**（该字段来自文件，
  不可信，未校验时可被用来写出任意扩展名的文件）
- 修复令牌轮换失效：API 拒绝时抛 `AuthError` 而非 `RuntimeError`，
  `ekey_for` 才能顺延到下一个令牌（此前的测试 mock 掉了该接缝，未能发现）
- 每个线程使用独立的 `requests.Session`（该类型不保证线程安全）
- 封面下载不再持锁做网络 I/O，避免所有 worker 被串行化
- 大整数 XOR 改为 4 MB 分块，峰值内存从「与文件等长」降下来
- 合并重复实现：周期性异或与原子写入统一到 `fne.util`

### 测试

- 90 项测试（可靠性、令牌缓存、输入校验、CLI 辅助函数、GUI 状态机等）
  - 在 Python 3.14 + tkinter 下：88 通过、2 跳过
  - 在无 tkinter 的 Python 3.13 下：84 通过、6 跳过（GUI 用例自动跳过，不会失败）
- 真机验证：4 个真实 `.ncm`（46–66 MB）与 10 个 `.mflac` / `.mgg`，
  解出的**音频帧与 Go 版逐字节一致**，标签与封面一致
- 发布链路验证：`python -m build` 产出 sdist + wheel，将 wheel 装进全新虚拟环境
  （依赖从 PyPI 现装，含更新版 `cryptography`）后复跑真实文件，结果一致

### 已知限制

- 仅 Windows 可用（进程内存扫描依赖 Win32 API，`fne.win32` 在导入期绑定 `kernel32`）；
  NCM 路径本身跨平台
- OGG 输出不写元数据（与 Go 版行为一致）
- 令牌缓存文件内含明文登录令牌，请勿分发
- GUI 需要 Python 自带 tkinter：命令行版不受影响，`fne-gui` 在缺少 tkinter 时
  会给出提示并指向命令行用法
