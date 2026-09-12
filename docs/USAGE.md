# FNE-py 使用教程

把网易云 `.ncm` 和 QQ 音乐 `.mflac` / `.mgg` 转成普通音频（FLAC / MP3 / OGG）。
音频**不重编码**，只解密，音质无损。

两种用法：图形界面和命令行，功能完全一样，用哪个都行。
下载版把两者做进了同一个 `fne.exe`：**双击开窗口，带参数走命令行**。

---

## 1. 单文件版（免安装，推荐）

从 [Releases](https://github.com/gumi-ri/FNE-py/releases) 下载 `fne.exe`（约 19 MB），
放到任意目录即可，**不用装 Python**。要求 Windows 10 / 11 x64。

```bat
:: 双击 = 打开图形界面，也可以显式指定
fne.exe

:: 命令行
fne.exe -i "C:\Music\VipSongsDownload" -o "D:\Music\Converted"

:: 自检：报告这个文件到底能不能正常用
fne.exe --selftest
```

两个目录要跟着 exe 走：`config.json`（配置）和 `.fne\`（令牌缓存）都放在 exe 同级，
换目录时一起复制。没有它们程序会自动重建。

> 第一次运行可能弹 SmartScreen 提示（exe 没有代码签名）。确认来源没问题后再运行；
> 拿不准就先跑 `fne.exe --selftest`，它会告诉你打包件是否完整。

---

## 2. 源码运行：准备环境（只做一次）

需要 Windows + Python 3.9 以上。

```bat
cd /d <FNE-py 目录>
python -m venv .venv
.venv\Scripts\pip install -e .
```

会装 5 个库：`cryptography` `mutagen` `psutil` `requests` `tqdm`，
并注册 `fne` 与 `fne-gui` 两个命令。

不想装进环境、只想跑一次：

```bat
.venv\Scripts\pip install cryptography mutagen psutil requests tqdm
.venv\Scripts\python -m fne -h
```

---

## 3. 图形界面（推荐日常使用）

```bat
.venv\Scripts\fne-gui
```

窗口里：

1. 选**输入目录**（放加密文件的目录）
2. 选**输出目录**（不存在会自动创建）
3. 需要的话勾上「包含子目录」、调整「并行任务」数
4. 点**开始转换**

进度条和日志会实时刷新。**中途可以点「停止」**——已经转好的文件会保留，
下次运行自动跳过，不会重头再来。

> 如果 `fne-gui` 提示找不到 tkinter：说明这个 Python 装的是精简版（不带 GUI 模块），
> 用下面的命令行方式即可，功能完全一样。单文件版自带 tkinter，不会遇到这个问题。

---

## 4. 命令行

```bat
.venv\Scripts\python -m fne -i "C:\Music\VipSongsDownload" -o "D:\Music\Converted"
```

单文件版把开头的 `.venv\Scripts\python -m fne` 换成 `fne.exe` 即可。

| 参数 | 说明 |
| --- | --- |
| `-i` | 输入目录（放加密文件的目录） |
| `-o` | 输出目录，不存在会自动创建 |
| `-r` | 连子目录一起扫 |
| `-j 16` | 并行线程数，默认 8，文件多可以调大 |
| `--verbose` | 出错时打印完整 traceback，反馈问题时附上它 |
| `-g` | 强制打开图形界面 |
| `--selftest` | 自检并退出（打包后排查用） |
| `-V` | 查看版本号 |

**可以随时中断重跑**：输出目录里已存在的同名文件会自动跳过。

`.lrc` 歌词、`.txt` 会被忽略，只处理加密音频。

退出码：全部成功是 `0`，有文件失败是 `1`，脚本里可以直接判断。

---

## 5. QQ 音乐文件的前置条件

| 文件类型 | 要求 |
| --- | --- |
| `.ncm`（网易云） | 无。纯本地解密 |
| `.mflac` / `.mgg`（QQ 音乐） | **客户端必须正在运行且已登录** |

QQ 音乐那条线需要向官方接口换解密密钥，令牌（`authst`）只存在于客户端进程内存里，
所以转之前先把 QQ 音乐开起来并确认是登录状态。

---

## 6. 用配置文件（可选）

不带 `-i` / `-o` 时，程序读**同级目录**的 `config.json`；没有配置文件就弹目录选择框。

```bat
copy config.json.example config.json
notepad config.json
.venv\Scripts\python -m fne
```

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

命令行参数优先级高于配置文件。GUI 启动时也会用它预填目录。

---

## 7. authst 令牌缓存（QQ 音乐线）

- **第一次**运行会扫一遍 QQ 音乐进程内存拿令牌，约 **4 秒**
- 拿到的候选（不只一个）存到 `.fne\authst_cache.json`——源码运行放在包目录旁，
  单文件版放在 **exe 同级目录**（都不写用户主目录）
- **之后**运行直接读缓存，约 **0.5 毫秒**，不再扫内存
- 某个令牌过期时自动换列表里下一个，**不需要重新登录**

想强制重新扫描：删掉 `.fne\authst_cache.json`（整个 `.fne` 文件夹也可以删，下次运行会自动重建）。

> 注意：该文件里是明文的登录令牌，别随便分享，也别提交进版本库（已在 `.gitignore` 里）。

---

## 8. 常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| `QQMusic.exe not found` | 客户端没开，或进程名不对 |
| `authst not found ... ensure ... logged in` | 客户端在但我们没取到令牌，重启客户端再试 |
| `no usable authst found` | 所有缓存令牌都失效了，重启客户端并删掉缓存文件 |
| `QQ Music API returned code ...` | 该文件所在账号无权限（比如没买这首） |
| `ModuleNotFoundError: No module named 'mutagen'` | 依赖没装，回 §2 |
| `无法启动图形界面：tkinter 未随这个 Python 安装` | 该 Python 不带 GUI 模块，改用命令行（§4），或直接用单文件版 |
| 输出比源文件大几十 KB | 正常，多了标签和封面 |
| 传了 `-i` 但还是弹出选目录窗口 | 该目录不存在。程序会退回目录选择框——**脚本里务必传存在的目录**，否则可能选到上次用过的目录 |
| 想确认 exe 是否完好 | `fne.exe --selftest`，看最后一行是否为 `selftest: PASS` |

---

## 9. 输出

文件写在 `-o` 指定的目录，扩展名按源文件决定：

| 源 | 输出 |
| --- | --- |
| `.ncm` | `.flac` 或 `.mp3`（按文件内记录） |
| `.mflac` | `.flac`（自动嵌封面） |
| `.mgg` | `.ogg` |

歌名 / 歌手 / 专辑 / 封面 / 文件创建时间都会保留。

---

## 10. 自检与打包（维护者）

`--selftest` 会真的开一个 Tk 窗口、往缓存目录写一个文件，并报出 Python 版本、
stdout 编码与各依赖是否打进去了。**打包漏东西在这里就能看出来**，不用等用户点到窗口：

```bat
pyinstaller --clean --noconfirm fne.spec
dist\fne.exe --selftest
```

`selftest: PASS` 才算可以分发。CI 会对每次构建产物强制执行同样的检查，
并校验 PE 头确实是 x64。

---

## 11. 发布前回归（维护者）

改动解密逻辑后，用 `tools/compare_audio.py` 确认音频帧没有变化：

```bat
python tools\compare_audio.py <新输出目录> <上一版输出目录>
```

它会跳过标签与封面块，只比对**音频帧**的 SHA1，输出 `AUDIO SAME` / `AUDIO DIFF`。
