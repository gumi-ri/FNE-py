# FNE-py 使用教程

把网易云 `.ncm` 和 QQ 音乐 `.mflac` / `.mgg` 转成普通音频（FLAC / MP3 / OGG）。
音频**不重编码**，只解密，音质无损。

两种用法：图形界面 `fne-gui`，命令行 `fne`。功能完全一样，用哪个都行。

---

## 1. 准备环境（只做一次）

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

## 2. 图形界面（推荐日常使用）

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
> 用下面的命令行方式即可，功能完全一样。

---

## 3. 命令行

```bat
.venv\Scripts\python -m fne -i "C:\Music\VipSongsDownload" -o "D:\Music\Converted"
```

| 参数 | 说明 |
| --- | --- |
| `-i` | 输入目录（放加密文件的目录） |
| `-o` | 输出目录，不存在会自动创建 |
| `-r` | 连子目录一起扫 |
| `-j 16` | 并行线程数，默认 8，文件多可以调大 |
| `--verbose` | 出错时打印完整 traceback，反馈问题时附上它 |
| `-V` | 查看版本号 |

**可以随时中断重跑**：输出目录里已存在的同名文件会自动跳过。

`.lrc` 歌词、`.txt` 会被忽略，只处理加密音频。

退出码：全部成功是 `0`，有文件失败是 `1`，脚本里可以直接判断。

---

## 4. QQ 音乐文件的前置条件

| 文件类型 | 要求 |
| --- | --- |
| `.ncm`（网易云） | 无。纯本地解密 |
| `.mflac` / `.mgg`（QQ 音乐） | **客户端必须正在运行且已登录** |

QQ 音乐那条线需要向官方接口换解密密钥，令牌（`authst`）只存在于客户端进程内存里，
所以转之前先把 QQ 音乐开起来并确认是登录状态。

---

## 5. 用配置文件（可选）

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

## 6. authst 令牌缓存（QQ 音乐线）

- **第一次**运行会扫一遍 QQ 音乐进程内存拿令牌，约 **4 秒**
- 拿到的候选（不只一个）存到包目录下的 `.fne\authst_cache.json`（不写用户主目录）
- **之后**运行直接读缓存，约 **0.5 毫秒**，不再扫内存
- 某个令牌过期时自动换列表里下一个，**不需要重新登录**

想强制重新扫描：删掉 `.fne\authst_cache.json`（整个 `.fne` 文件夹也可以删，下次运行会自动重建）。

> 注意：该文件里是明文的登录令牌，别随便分享，也别提交进版本库（已在 `.gitignore` 里）。

---

## 7. 常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| `QQMusic.exe not found` | 客户端没开，或进程名不对 |
| `authst not found ... ensure ... logged in` | 客户端在但我们没取到令牌，重启客户端再试 |
| `no usable authst found` | 所有缓存令牌都失效了，重启客户端并删掉缓存文件 |
| `QQ Music API returned code ...` | 该文件所在账号无权限（比如没买这首） |
| `ModuleNotFoundError: No module named 'mutagen'` | 依赖没装，回 §1 |
| `无法启动图形界面：tkinter 未随这个 Python 安装` | 该 Python 不带 GUI 模块，改用命令行（§3） |
| 输出比源文件大几十 KB | 正常，多了标签和封面 |

---

## 8. 输出

文件写在 `-o` 指定的目录，扩展名按源文件决定：

| 源 | 输出 |
| --- | --- |
| `.ncm` | `.flac` 或 `.mp3`（按文件内记录） |
| `.mflac` | `.flac`（自动嵌封面） |
| `.mgg` | `.ogg` |

歌名 / 歌手 / 专辑 / 封面 / 文件创建时间都会保留。

---

## 9. 发布前回归（维护者）

改动解密逻辑后，用 `tools/compare_audio.py` 确认音频帧没有变化：

```bat
python tools\compare_audio.py <新输出目录> <上一版输出目录>
```

它会跳过标签与封面块，只比对**音频帧**的 SHA1，输出 `AUDIO SAME` / `AUDIO DIFF`。
