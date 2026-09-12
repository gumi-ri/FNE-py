# FNE-py

![CI](https://github.com/gumi-ri/FNE-py/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

把网易云音乐的 `.ncm` 和 QQ 音乐的 QMC2 加密文件（`.mflac` / `.mgg`）转回标准音频，
**不重编码**，音质无损，元数据与封面原样保留。

同一套解密逻辑配两个前端：给日常使用的图形界面，和给脚本 / 批处理准备的命令行。

> 解出的音频流与上游 Go 实现**逐字节一致**——已用真实 `.ncm`（46–66 MB）和
> 10 个 `.mflac` / `.mgg` 交叉验证，音频帧 SHA1 相同，元数据与封面也一致。
> 同样的比对在单文件 `fne.exe` 上重跑过：9/9 FLAC 音频帧、OGG 整文件 SHA1 均一致。
> 上游 Go 版本只作参照，已不在此仓库内。

---

## 特性

- **免环境单文件** —— 一个约 19 MB 的 `fne.exe`，双击就是图形界面，带参数就是命令行
- **两个前端，一套逻辑** —— `fne-gui` 图形界面、`fne` 命令行，共用同一个转换管线
- **不重编码** —— 只解密音频流并重接元数据块，音质原样
- **增量转换** —— 输出目录里已存在的同名文件自动跳过，可随时中断重跑
- **保留元数据** —— 歌名 / 歌手 / 专辑 / 内嵌封面，以及文件创建时间
- **多线程** —— 默认 8 个 worker；QQ 音乐接口的并发与延迟限流可调
- **令牌自动顺延** —— 登录令牌失效时自动换下一个候选，不用重新登录
- **原子写入** —— 临时文件 + 原子改名，中断或磁盘写满不会留下半截文件

## 环境要求

| 用法 | 需要什么 |
| --- | --- |
| 单文件 `fne.exe` | **什么都不用装**，Windows 10 / 11 x64 |
| 从源码运行 | Windows + Python 3.9 或更高 |

QMC2 路径必须读 QQ 音乐进程的内存，用到 Win32 API，所以目前只有 Windows 版本。

## 安装

### 方式一：下载单文件（推荐）

从 [Releases](https://github.com/gumi-ri/FNE-py/releases) 下载 `fne.exe`，约 19 MB，
**免安装、免 Python 环境**。同一个文件既是图形界面也是命令行：

```bash
fne.exe                                        # 双击或直接运行 = 打开窗口
fne.exe -i "C:/Music/VipSongsDownload" -o "D:/Music/Converted"
fne.exe --selftest                             # 自检：报告这个文件能不能正常用
```

换电脑 / 换目录时把 `config.json` 和 `.fne/` 一起带走——它们放在 exe 同级目录。

### 方式二：从源码安装

```bash
git clone https://github.com/gumi-ri/FNE-py.git
cd FNE-py
pip install .
```

安装后会注册两个命令：`fne`（命令行）和 `fne-gui`（图形界面）。

开发时建议用可编辑安装，改代码立即生效：

```bash
pip install -e ".[dev]"      # 含 pytest / pyflakes
```

不安装也能直接跑：`python -m fne -h`。

### 自己打包单文件

```bash
pip install pyinstaller
pyinstaller --clean --noconfirm fne.spec     # 产出 dist/fne.exe
dist/fne.exe --selftest                      # 先自检再分发
```

`--selftest` 会真的开一个 Tk 窗口、往缓存目录写一个文件，并报出 Python 版本、
stdout 编码和各依赖模块是否打进去了——打包漏东西在这里就能看出来，不用等用户点到窗口。

## 使用

### 图形界面

```bash
fne-gui
```

选好输入 / 输出目录，点「开始转换」。窗口里可以调并行任务数、是否包含子目录，
进度条和日志实时刷新。中途点「停止」——**已经转好的文件会保留**，下次运行自动跳过。

### 命令行

```bash
# 最基本：指定输入输出目录
fne -i "C:/Music/VipSongsDownload" -o "D:/Music/Converted"

# 递归扫描子目录，16 个并行 worker
fne -i <输入> -o <输出> -r -j 16

# 排障：失败时打印完整 traceback
fne -i <输入> -o <输出> --verbose
```

| 参数 | 说明 |
| --- | --- |
| `-i, --input` | 输入目录 |
| `-o, --output` | 输出目录（不存在会自动创建） |
| `-c, --config` | 指定配置文件路径 |
| `-r, --recursive` | 递归扫描子目录 |
| `-j, --jobs` | 并行 worker 数，默认 8 |
| `--api-concurrent` | QQ 音乐接口并发数，默认 3 |
| `--api-delay-min` / `--api-delay-max` | 接口随机延迟区间（毫秒），默认 200 / 800 |
| `--verbose` | 失败时打印完整 traceback |
| `-g, --gui` | 强制打开图形界面 |
| `--selftest` | 自检并退出（打包后排查用） |
| `-V, --version` | 打印版本号 |

单文件版本把上面的 `fne` 换成 `fne.exe` 即可。

**不带任何参数**时会打开图形界面——这正是让一个可执行文件通吃双击和命令行的规则。
退出码：全部成功为 `0`，有文件失败为 `1`，方便在脚本里判断。

### 配置文件

在程序同级目录放 `config.json`（参考 `config.json.example`）：

```json
{
  "inputFolder": "C:/Music/VipSongsDownload",
  "outputFolder": "D:/Music/Converted",
  "recursive": false,
  "apiConcurrent": 3,
  "apiDelayMin": 200,
  "apiDelayMax": 800
}
```

字段全部可选，命令行参数的优先级高于配置文件。

## 前置条件

- **网易云** —— 需要 VIP 下载到本地的 `.ncm` 文件
- **QQ 音乐** —— 客户端必须**正在运行且已登录**，转换时要从进程内存里读取 `authst` 令牌

### authst 令牌缓存

`authst` 是 QQ 音乐的登录令牌，只存在于客户端进程内存里，读取它既慢（要遍历整个
进程地址空间）又容易读错（同一进程里往往同时躺着好几个过期或模板化的副本）。做法是：

1. **收集** —— 扫描进程内存与 cookie 文件，按可信度取出**全部**候选令牌并排序
   （优先 `Q_H_L_` 前缀、靠近当前登录 UIN、长度更长者）
2. **记录** —— 候选列表按可信度写入 `.fne/authst_cache.json`，最近读取的排在最前
   （源码运行在包目录旁，单文件版本在 exe 同级目录）
3. **优先用缓存** —— 下次运行直接取列表第一个，**完全跳过内存扫描**（约 0.5 ms）
4. **失效顺延** —— 某个令牌被服务端拒绝时自动从缓存摘掉并换下一个，全部用完才重新扫描

第一次运行付一次扫描成本，之后基本秒开。

> ⚠️ 缓存文件里是**明文登录令牌**，不要分发该文件。删掉它即可重置。

## 转换逻辑

### NCM（网易云）

| 优先级 | 输出 | 说明 |
| --- | --- | --- |
| 1 | FLAC + 元数据 | Vorbis Comment + 内嵌封面 |
| 2 | MP3 + 元数据 | ID3v2.3 标签 + 封面 |

### QMC2（QQ 音乐）

| 输入扩展名 | 输出 |
| --- | --- |
| `.mflac` `.mflac0` `.mflach` | `.flac`（自动嵌封面） |
| `.mgg` `.mgg0` `.mgg1` `.mggl` | `.ogg` |

## 依赖

| 库 | 版本 | 用途 |
| --- | --- | --- |
| `cryptography` | >=42 | AES-ECB（NCM 密钥与元数据解密） |
| `mutagen` | >=1.47 | FLAC Vorbis Comment / Picture、MP3 ID3v2 标签 |
| `psutil` | >=5.9 | 进程枚举（定位 QQMusic.exe） |
| `requests` | >=2.31 | QQ 音乐接口与封面下载 |
| `tqdm` | >=4.66 | 命令行进度条 |

GUI 用 Python 自带的 `tkinter`，**不引入额外依赖**。

**仍在手写的部分**（没有可用的现成库）：

| 模块 | 说明 |
| --- | --- |
| `tea.py` | TEA 与腾讯的 TC-TEA（`oi_symmetry_decrypt2`）变体 |
| `qmc2.py` | QMC2 Map / 分段 RC4 流密码，QQ 音乐私有算法 |
| `win32.py` | `ReadProcessMemory` + `VirtualQueryEx`；写 NTFS 文件创建时间 |

## 性能

Go 版逐字节异或，照搬成 Python 会慢到分钟级。两个流密码都有周期性，因此：

- **NCM key box** —— 密钥流周期 256 字节，预生成 256 字节表后整块做 `int` 异或
- **QMC2 Map** —— 偏移以 0x7FFF 为周期，预生成 32768 字节表后整块异或
- **QMC2 RC4** —— 每个 5120 字节段的丢弃值只有 512 种可能，按丢弃值缓存密钥流，
  跨文件的数千段复用（全文件 23.4 MB 实测 0.62 s，约 38 MB/s）

## 项目结构

```
fne/
  launcher.py   唯一入口：决定走命令行还是图形界面，并做打包自检
  cli.py        参数解析、配置、扫描、并发调度（两个前端共用）
  gui.py        tkinter 图形界面
  qqmusic.py    QQ 音乐凭证、令牌缓存、HTTP 接口
  qmc2.py       QMC2 容器解密（Map / 分段 RC4）
  ncm.py        NCM 容器解密（AES-ECB + 周期密钥流）
  aes.py        AES-ECB（cryptography 封装）
  tea.py        TEA / 腾讯 TC-TEA
  tags.py       元数据写入（mutagen 封装）
  win32.py      进程枚举、跨进程读内存、文件时间
  util.py       路径锚定、周期性异或、原子写入、UTF-8 输出
fne.spec        PyInstaller 单文件打包配置
tests/          自动化测试
tools/          发布回归用的音频帧比对工具
docs/           使用教程与审计报告
```

## 测试

```bash
pip install -e ".[dev]"
pytest                       # 自动化测试
pyflakes fne tests tools     # 静态检查
```

覆盖内容：AES 标准向量（FIPS-197）、两种 QMC2 密码与朴素参考实现比对、原子写入、
authst 提取（4 种序列化布局 × 2 种编码）、令牌缓存与失效顺延、NCM 合成容器往返与
畸形输入拒绝、CLI 扫描与增量跳过、GUI 状态机与无 tkinter 时的降级。

> 真机验证：4 个真实 `.ncm`（46–66 MB）与 10 个 `.mflac` / `.mgg`，音频帧与 Go 版
> **逐字节一致**。详见 [docs/AUDIT.md](docs/AUDIT.md)。

## 已知限制

- 仅 Windows 可用。NCM 路径本身是跨平台的，但 `win32` 模块在导入期就绑定了
  `kernel32`，所以整个包目前只能在 Windows 上运行
- 封面只嵌 FLAC；OGG 不写元数据
- 元数据块布局与上游 Go 版略有差异（本版不留 PADDING、标签合并为单块）——音频数据不受影响

## 合规声明

本工具只用于把**你已合法获得**的会员下载文件转成通用格式供个人设备播放。
请勿用于分发受版权保护的内容。所有解密都在本地完成，不上传任何文件。

## 许可证

MIT，见 [LICENSE](LICENSE)。

解密算法与容器格式解析源自 [zyoung11/FNE](https://github.com/zyoung11/FNE)（MIT）
的 Go 实现，版权归原作者所有；Python 版本为独立重写。
