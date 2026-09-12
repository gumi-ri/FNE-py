# FNE-py 审计报告

- 审计日期：2026-09-12
- 对象：FNE-py 0.1.0（上游 Go 版 FNE 的 Python 移植）
- 范围：架构、可靠性、可维护性、发布就绪度
- 验证基线：108 项自动化测试 + 真机样本（4 个 `.ncm` 46–66 MB、10 个 `.mflac` / `.mgg`）
  + 全新虚拟环境 wheel 安装验证 + 单文件 `fne.exe` 端到端验证

---

## 一、架构

```
fne/
  __main__.py   入口（python -m fne）
  cli.py        参数解析、配置、扫描、并发调度
  qqmusic.py    QQ 音乐凭证、令牌缓存、HTTP API
  qmc2.py       QMC2 容器解密（Map / 分段 RC4）
  ncm.py        NCM 容器解密（AES-ECB + 周期密钥流）
  aes.py        AES-ECB（cryptography 封装）
  tea.py        TEA / 腾讯 TC-TEA（无可用库）
  tags.py       元数据写入（mutagen 封装）
  win32.py      进程枚举、跨进程读内存、文件时间
  util.py       周期性异或、原子写入          ← 本次新增
```

依赖方向单向、无环：`cli → {ncm, qmc2} → {qqmusic, tags, win32, util, aes}`。
`ncm` 不需要网络与凭证，`qmc2` 需要——这条边界是移植版最正确的一个决定：
两种容器互不牵连，NCM 路径天然跨平台。

**结论**：架构健康，无需重构。本次只做了一处收敛：把 `ncm` 与 `qmc2` 各自实现的
周期性异或、以及散落各处的文件写入，统一到 `util.py`。

---

## 二、发现的问题

### P0 —— 导致错误行为，必须修

#### 1. 令牌轮换完全失效（`qqmusic.py:497`）

`fetch_ekey` 在接口返回非 0 时抛 `RuntimeError`，而调用方 `ekey_for` 捕获的是
`AuthError`——两者不匹配，`except AuthError` 永不触发：

```python
for _ in range(MAX_AUTH_ATTEMPTS):
    creds = get_credentials()
    try:
        return fetch_ekey(creds, filename, songmid)
    except AuthError as exc:      # ← 永远等不到
        mark_authst_invalid(creds.authst)
```

**影响**：对外承诺的「令牌过期自动顺延」实为死代码。令牌一旦失效，转换直接失败，
用户必须手动删除缓存文件。

**为何没被测出**：`test_ekey_for_rotates_past_a_stale_token` 把 `fetch_ekey`
整体 mock 掉了，测试两边自洽、与真实实现不符。

**修复**：改抛 `AuthError`；新增 4 项**打在真实接缝上**的测试（只 stub HTTP 层
`_post`），覆盖「令牌被拒 → 抛 AuthError」「无权限 → 不误判为令牌问题」「真实轮换
顺序正确」「轮换后缓存被清理」。

#### 2. 半截输出文件会被永久跳过（`ncm.py` / `qmc2.py`）

转换直接 `open(path, "wb").write(audio)` 写进输出目录，之后才打标签。若写入中途
崩溃、磁盘写满或 Ctrl+C，会留下一个不完整但**文件名正确**的产物；而增量逻辑只按
文件名判断「已转换」，该文件从此被永久跳过——用户拿到一个损坏的音频，且没有任何
提示。

**修复**：新增 `util.write_atomic()`：写临时文件 + `os.replace` 原子改名，文件只
在完整时才出现，失败时清理临时文件。

### P1 —— 应当修

#### 3. 输出扩展名未校验（`ncm.py`）

```python
fmt = (meta.get("format") or "flac").lower()
output_path = os.path.join(output_dir, base + "." + fmt)
```

`format` 来自被解密文件自身的元数据，属于**不可信输入**。构造一个
`format: "../../evil"` 的 NCM 即可让程序在输出目录之外写出任意扩展名的文件。

**修复**：白名单校验，只允许 `flac` / `mp3`，否则报错。

#### 4. 封面下载持锁做网络 I/O（`qqmusic.py:533`）

`fetch_album_cover` 整个函数体都在 `_cover_lock` 内，其中包含两次网络请求
（含 200–800 ms 限流延迟）。8 个 worker 会全部串行阻塞在锁上。

**修复**：锁只保护字典读写，网络请求移到锁外。

**实测收益**：同一批 10 个文件从 **18.1 s 降到 10.4 s**。

#### 5. `requests.Session` 跨线程共享（`qqmusic.py:469`）

模块级单例 `_session` 被所有 worker 共用，而该类型并未声明线程安全。

**修复**：改为 `threading.local()`，每线程一个 Session。

#### 6. 配置读取失败时抛裸异常（`qqmusic.py:259`）

`QQMusicServiceConfig.ini` 不存在时直接抛 `FileNotFoundError`，用户看到的是一串
系统路径错误，而不是「请先启动 QQ 音乐并登录」。

**修复**：包装为可操作的提示，附文件路径与原因。

#### 7. 死代码会删除用户目录中的文件（`cli.py`）

```python
def cleanup_bad_files(directory):
    """Remove id3v2 library leftovers from earlier runs."""
    for name in os.listdir(directory):
        if name.lower().endswith(".mp3-id3v2"):
            os.remove(...)
```

手写 ID3v2 模块时代的遗留（Go 版同样存在）。改用 mutagen 后不会再产生这类文件，
该函数唯一的作用就是**可能删掉用户输出目录里的东西**。

**修复**：删除函数与调用点。

#### 8. 增量判断把 0 字节文件当成已完成（`cli.py`）

**修复**：`build_existing_set` 跳过大小为 0 的文件。

#### 9. 边界判断写错（`qqmusic.py:120`）

```python
end = _find_any(s, ",&\r\n }", i)
return s[i:end] if end > 0 else s[i:]
```

`_find_any` 返回 -1 表示「未找到」，但也能合法返回 0（值为空）。用 `> 0` 判断会把
「空值」误当成「无终止符」，返回整段剩余字符串而非空串。目前靠下游
`is_valid_authst` 兜住，未造成实际影响，但属于隐患。

**修复**：改为 `>= 0`。

#### 10. 同一字段解析两次（`qmc2.py`）

`parse_musicex_footer` 内部已读 `footer_size` 用于校验，`convert_file` 又到同一
偏移重读一遍。

**修复**：`MusicexInfo` 增加 `footer_size` 字段，消除重复解析。

#### 11. 输入缺少长度校验（`ncm.py`）

文件被截断时 `struct.unpack_from` 抛的是
`struct.error: unpack_from requires a buffer of at least 4 bytes`，对用户毫无帮助。

**修复**：新增 `_read_u32` 哨兵，逐段校验并给出 `truncated ncm: ...` 的明确错误；
同时确保各块长度不会越过文件末尾。

### P2 —— 记录但未改

| 项 | 说明 | 处理 |
| --- | --- | --- |
| `win32.MAX_REGION_SIZE = 200 MB` | 扫描跳过超过 200 MB 的内存区域，理论上可能漏令牌 | 实测能找到 6 个候选，保留 |
| `RateLimiter` 持信号量 sleep | 语义是「每 N 个请求后延迟」，非严格令牌桶 | 与上游一致，保留 |
| 错误信息为英文 | 与中文文档不一致 | 遵循工具惯例，保留 |
| `find_best_authst` 用 `>= 30`、`is_valid_authst` 用 `>= 20` | 冗余但不冲突 | 保留 |

---

## 三、可维护性

### 已处理

- **重复实现** —— `ncm._xor_stream` 与 `qmc2._xor` 各写了一份大整数异或
  → 统一到 `util.xor_repeating`，并改为分块以降低内存峰值
- **魔法数字** —— `ncm.py` 的 `22`（元数据包装长度）、`9`（CRC32 + 保留位）
  → `META_PREFIX` / `AUDIO_GAP` 常量
- **过时文档** —— `test_ncm.py` 声称「本机无真实样本」，实际已有
  → 改写为说明「合成测试锁算法、真机样本锁格式」的分工
- **测试盲区** —— 见 P0-1，新增打在真实接缝上的测试
- **缺失的工程文件** —— `pyproject.toml` / `LICENSE` / `CHANGELOG.md` 本次补齐

### 一个值得记下的教训

P0-1 与「NCM 元数据前缀」两次翻车，根因相同：**测试用具与被测代码共享了同一个
错误假设**。

- 令牌轮换：测试 mock 掉了真正会出错的那一层接缝
- NCM 元数据：测试合成器按「明文前缀 + 加密载荷」构造，而真实文件是「整体加密」。
  已用真实样本确认：磁盘字节 `RUPC\x08\x06\x1aK...` → 解密后
  `163 key(Don't modify):`

两次都是**真机跑一遍立刻暴露**。合成测试能锁住算法，却锁不住格式假设；
凡涉及文件格式，必须有真实样本把关。

---

## 四、发布就绪度

### 已补齐

| 项 | 状态 |
| --- | --- |
| `pyproject.toml` | ✅ 元数据 + 依赖 + `fne` / `fne-gui` 入口 + pytest 配置 |
| 版本号单一来源 | ✅ `fne/__init__.py` 的 `__version__`，`-V/--version` 可查 |
| `LICENSE` | ✅ MIT（保留上游版权，符合派生作品要求） |
| `CHANGELOG.md` | ✅ |
| Python 版本声明 | ✅ `requires-python = ">=3.9"`，CI 覆盖 3.9 / 3.13 |
| 排障开关 | ✅ `--verbose` 打印完整 traceback |
| 测试 | ✅ 108 项，pyflakes 零告警 |
| sdist / wheel | ✅ `python -m build` 产出正常，元数据完整 |
| 干净环境安装 | ✅ 全新 venv 装 wheel 后功能与源码版一致 |
| GUI | ✅ `fne-gui`（tkinter/ttk，零额外依赖），缺 tkinter 时降级提示 |
| 单文件 exe | ✅ PyInstaller onefile，19 MB，免 Python 环境，x64，自带版本资源 |
| 打包自检 | ✅ `--selftest` 真开 Tk 窗口 + 写缓存目录，CI 对产物强制执行 |
| CI | ✅ GitHub Actions：lint + 测试 + 构建 + 单文件 exe 构建自检 + tag 发 Release |

### 发布前建议

1. ~~**打包验证**~~ —— ✅ **已完成**：`python -m build` 产出 sdist + wheel，
   装进全新 venv（依赖从 PyPI 现装）后 `fne -V` 可用、真实文件转换结果一致。
   详见第六节。
2. ~~**CI**~~ —— ✅ **已完成**：`.github/workflows/ci.yml` 跑 pyflakes、Windows 上
   跨 3.9/3.13 的测试、构建并校验 wheel，打 tag 时发 Release。
   真机部分（需 QQ 音乐客户端与授权样本）仍保留为发布前手工清单
3. **手工回归清单**（每次发布）：
   - `.ncm` 转换 → 音频帧与上一版一致
   - `.mflac` / `.mgg` 转换 → 音频帧一致
   - 删除 `.fne/authst_cache.json` → 冷启动能重建，且不写用户主目录
   - 令牌失效场景 → 确认自动顺延（可临时把首个候选改成无效值）
   - 音频帧比对用 `tools/compare_audio.py`
   - 单文件 exe：`--selftest` 必须 PASS，且**管道/重定向**下输出中文不崩
     （`fne.exe -i <路径> > log.txt`，这是历史上翻过车的地方）
   - 单文件 exe：双击能开窗口，`.fne/` 落在 exe 同级目录
4. ~~**合规**~~ —— ✅ README 已加入合规声明（仅用于已合法获得的文件，本地解密）
5. **未做** —— Windows 之外的平台（QMC2 路径依赖 Win32 API）。NCM 路径本身可跨
   平台，若要做需先把 `win32` 抽成可替换后端

---

## 五、回归结果

| 项目 | 结果 |
| --- | --- |
| 自动化测试 | 108 项：Python 3.14 + tkinter 下 108 通过 / 0 跳过；无 tkinter 的 3.13 下 99 通过 / 9 跳过 |
| pyflakes | 零告警 |
| 真机 `.ncm`（4 个，46–66 MB） | 4 / 4 成功，音频帧与上游 Go 版**逐字节一致** |
| 真机 `.mflac` / `.mgg`（10 个） | 10 / 10 成功，9 / 9 FLAC 音频帧**逐字节一致**；OGG 整文件 SHA1 一致 |
| 单文件 `fne.exe` 复跑上述真机样本 | 10 / 10 成功，9 / 9 FLAC 音频帧 + OGG SHA1 与源码构建**一致** |
| QMC2 批处理耗时 | 18.1 s → 10.4 s（封面锁粒度修复的副作用） |
| 令牌缓存冷启动 | 3.7 s 重建，写入 `FNE-py/.fne/`，用户主目录未被创建 |
| 冻结版令牌缓存 | 冷启动 20.4 s（含首次内存扫描），缓存落在 **exe 同级目录**，用户主目录未被创建 |

---

## 六、发布链路验证

审计建议的「打包验证」已实际执行。单文件 exe 的验证见第七节。

### 步骤与结果

| 步骤 | 结果 |
| --- | --- |
| `python -m build --no-isolation` | ✅ 产出 `fne-0.1.0.tar.gz` + `fne-0.1.0-py3-none-any.whl`，11 个模块 + LICENSE + 入口点齐全 |
| wheel 元数据 | ✅ MIT（含上游 2025 Zyoung + 2026 FNE-py 两段版权）、`Requires-Python >=3.9`、5 项依赖 + `dev` extra |
| sdist 内容 | ✅ 含 `tests/`、`README.md`、`LICENSE`、`pyproject.toml`；wheel 不含 `tests/`（正确） |
| 全新 venv 安装（PyPI 现装依赖） | ✅ `cryptography 50.0.1`、`mutagen 1.48.1`、`psutil 7.2.2`、`requests 2.34.2`、`tqdm 4.70.1` |
| 模块来源 | ✅ `site-packages\fne`，非源码树——确认安装链路自洽 |
| `fne -V` / `--help` | ✅ 输出 `fne 0.1.0`，参数表完整 |
| 真实 `.ncm` 转换 | ✅ 4 / 4 成功，音频帧与 Go 版**逐字节一致** |
| 真实 `.mflac` 转换 | ✅ 10 / 10 成功（9 FLAC + 1 OGG），9 / 9 FLAC 音频帧**逐字节一致** |
| 用户主目录污染检查 | ✅ `%USERPROFILE%\.fne` 未被创建 |

安装版把缓存写到了 `site-packages\.fne\authst_cache.json`——这是「缓存放在包目录旁、
不写用户文件」这一设计的直接结果。系统级安装时该目录只读，因此**补了一项针对性测试**
确认此时只是降级（当次运行仍用内存中的候选列表），不会中断转换：

- `test_unwritable_cache_does_not_break_conversion`
- `test_corrupt_cache_file_is_ignored`
- `test_cache_round_trips_across_processes`

### 顺带修掉的两处

- `.gitignore` 原先漏了 `build/`、`dist/`、`*.egg-info/`——打包后生成物会被误提交，已补
- `python -m build` 在 `dist/` 留下的临时目录已清理

### 仍未做

- **非 Windows 平台未验证**：QMC2 路径依赖 Win32 API，需先把 `win32` 抽成可替换后端

---

## 七、单文件构建验证

发布形态改为「下载即用」后，新增 PyInstaller 单文件构建，并按第六节的同样口径做了
端到端验证——**打包件必须自己证明自己**，源码层面的测试覆盖不了它。

### 步骤与结果

| 步骤 | 结果 |
| --- | --- |
| `pyinstaller --clean --noconfirm fne.spec` | ✅ 产出 `dist/fne.exe`，19.1 MB，单文件 |
| PE 架构 | ✅ `0x8664`（x86-64） |
| 版本资源 | ✅ 从 `fne/__init__.py` 读出并写入，属性页显示 `FNE-py 0.1.0` |
| `fne.exe --selftest` | ✅ `selftest: PASS`：5 项依赖齐全、Tk 窗口可开、缓存目录可写 |
| CLI 路径（`-i` / `-o`） | ✅ 10 / 10 成功，输出与源码构建一致 |
| GUI 路径（无参数） | ✅ 窗口标题 `FNE 0.1.0 · 音乐解密转换`，类名 `TkTopLevel`，可见 |
| 控制台隐藏 | ✅ 自建控制台被隐藏；从 shell 启动时不动用户的终端 |
| 令牌缓存落点 | ✅ `.fne/authst_cache.json` 落在 exe 同级目录，冷启动 20.4 s → 二次 13.7 s |
| 环境洁净度 | ✅ 在 `PYTHONPATH` / `PYTHONHOME` / `PYTHONIOENCODING` 全清空的 shell 中运行通过 |
| 音频一致性 | ✅ 9 / 9 FLAC 音频帧 SHA1 与源码构建一致，OGG 整文件 SHA1 一致 |

### 这一轮抓到的两个问题

#### P0：冻结版输出非 ASCII 即崩溃

**现象**：`fne.exe -i <路径> > log.txt` 直接抛 `UnicodeEncodeError` 并终止进程，
用户看到的是 `Failed to execute script 'launcher'` 加一段 traceback，而不是错误提示。
触发点是中文的目录选择提示。

**根因**：打包后的解释器**不运行在 UTF-8 模式**（`sys.flags.utf8_mode == 0`）。
stdout 是控制台时 Python 走 Windows 控制台 API，编码无所谓；一旦重定向到管道或文件，
就退回系统 ANSI 代码页（本机 cp1252），任何中文写法都会编码失败。

**为什么测试没抓到**：本机开发用的解释器恰好都开着 UTF-8 模式（`utf8_mode == 1`，
`getpreferredencoding()` 返回 `utf-8`），于是**整套测试对这个问题完全免疫**。
这不是"漏了一个用例"，而是测试环境与被测环境不一致——正是打包件需要单独验证的理由。

**修复**：入口处统一把标准流重设为 UTF-8（`errors="replace"` 兜底），
并让 `--selftest` 报出 `output enc`，CI 断言必须为 `utf-8`。
同类问题（例如将来引入新的中文输出路径）从此会在构建阶段就被挡住。

#### P1：Tcl 偶发初始化失败被误判为构建损坏

**现象**：`--selftest` 概率性报 `tk window : FAILED`，错误说某个 ttk 主题脚本
「无法读取」，而该文件确实在磁盘上。

**定位**：把一个 Tcl 解释器创建并销毁后，紧接着创建下一个会偶发失败；
同进程内**紧接着再试一次就成功**。概率约 1/6，且与是否被 pytest 捕获输出无关，
只与"前一个解释器刚被销毁"有关——单独跑 15 次全部成功，只有先建后毁再建才复现。

**修复**：开窗口重试一次（`fne.gui.create_root`）。偶发抖动被吸收；
真正不可用（无显示、缺 Tcl 数据目录）两次都失败，仍会稳定上报。
此前 `test_gui.py` 的 fixture 因此偶尔整组跳过，修复后不再跳过。

### 未做

- **代码签名**：无证书，首次运行会触发 SmartScreen 提示。已在文档中说明，
  并建议用户核对 SHA256 或先跑 `--selftest`
- **`-i` 指向不存在目录时会退回图形目录选择框**：在无人值守脚本里可能选到
  「上次用过的目录」。已在 CHANGELOG「已知限制」中说明；是否改为直接报错属于
  产品取舍（桌面用户确实可能想要那个选择框），未擅自改变行为
