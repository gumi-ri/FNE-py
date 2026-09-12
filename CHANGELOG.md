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
- **免环境单文件 `fne.exe`** —— PyInstaller 单文件打包，约 19 MB，Windows 10+ x64，
  不需要安装 Python。同一个文件双击即开图形界面、带参数即为命令行；
  已写入版本资源（属性页可见），并校验为 x64
- **`--selftest` 自检** —— 冻结后真的开一个 Tk 窗口、往缓存目录写一个文件，
  并报出 Python 版本、stdout 编码与各依赖是否打进去了；CI 对构建产物强制执行
- **`fne.launcher` 统一入口** —— `python -m fne`、`fne` 控制台脚本与单文件 exe
  走同一条分发规则：`--selftest` > `-g/--gui` > 无参数开窗口 > 其余走命令行

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

- **修复：冻结版输出非 ASCII 时崩溃** —— 打包后的解释器不运行在 UTF-8 模式，
  一旦 stdout 不是控制台（管道、重定向到文件、CI 日志），Python 会退回系统 ANSI
  代码页，任何中文消息都触发 `UnicodeEncodeError` 并直接终止进程。开发用解释器
  恰好开启 UTF-8 模式，所以整套测试对此完全免疫；实际是在
  `fne.exe -i <路径> > log.txt` 这种日常用法里才暴露出来。
  现在入口处统一把标准流改成 UTF-8（`errors="replace"` 兜底），CI 上的自检也会
  校验 `output enc : utf-8`
- **修复：Tcl 偶发初始化失败被误判为构建损坏** —— Windows 上一个 Tcl 解释器
  刚被销毁后紧接着创建下一个，会偶发地把一个确实存在于磁盘上的 ttk 主题脚本
  报成「无法读取」，而下一次尝试就成功。原先「开窗口」只试一次，于是健康的构建
  会被自检判死、双击也可能看到启动失败的提示。改为重试一次：偶发抖动被吸收，
  真正不可用（无显示、缺 Tcl 数据目录）仍会稳定上报
- **冻结模式的路径锚定** —— `app_dir()` 在冻结时指向 exe 所在目录。单文件包会
  解压到退出即删的临时目录，若仍按 `__file__` 定位，`authst` 缓存会写进临时目录
  然后被删掉，每次运行都静默退化成约 4 秒的内存扫描
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

- 108 项测试（可靠性、令牌缓存、输入校验、CLI 辅助函数、GUI 状态机、
  入口分发与自检等）
  - 在 Python 3.14 + tkinter 下：108 通过、0 跳过
  - 在无 tkinter 的 Python 3.13 下：99 通过、9 跳过（GUI 用例自动跳过，不会失败）
- 真机验证：真实 `.ncm`（46–66 MB）与 10 个 `.mflac` / `.mgg`，
  解出的**音频帧与上游 Go 版逐字节一致**，标签与封面一致
- 单文件 exe 复跑同一套真机验证：10/10 成功，9/9 FLAC 音频帧与
  源码构建产物一致，OGG 整文件 SHA1 一致；冷启动（无令牌缓存）也能
  从 QQ 音乐进程内存取到凭证，缓存正确落在 exe 同级目录
- 发布链路验证：`python -m build` 产出 sdist + wheel，将 wheel 装进全新虚拟环境
  （依赖从 PyPI 现装，含更新版 `cryptography`）后复跑真实文件，结果一致

### 已知限制

- 仅 Windows 可用（进程内存扫描依赖 Win32 API，`fne.win32` 在导入期绑定 `kernel32`）；
  NCM 路径本身跨平台
- OGG 输出不写元数据（与 Go 版行为一致）
- 令牌缓存文件内含明文登录令牌，请勿分发
- GUI 需要 Python 自带 tkinter：命令行版不受影响，`fne-gui` 在缺少 tkinter 时
  会给出提示并指向命令行用法
- 单文件 exe 未做代码签名（无证书），首次运行可能触发 Windows SmartScreen 提示；
  不确定时可先跑 `fne.exe --selftest` 或核对 Release 页的 SHA256
- `-i` 指向不存在的目录时，会退回图形目录选择框而非直接报错。在无人值守的
  脚本里这意味着可能选中「上次用过的目录」，请保证 `-i` 指向真实存在的目录
  （脚本里最好同时传 `-o`）
