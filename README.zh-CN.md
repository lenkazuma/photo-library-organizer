# 照片库整理工具

[English](README.md) | [简体中文](README.zh-CN.md)

个人照片管理脚本的开源归档：按月份整理、人工复核文字／聊天图片，以及离线搜索照片元数据。
仓库只包含代码和虚构示例，不包含个人照片、地点历史或整理日志。

## 已有功能

- 合并 `YYYYMM`、`YYYYMM_a`、`YYYYMM-*` 月份目录；嵌套时以最深一层月份为准。
  月份来自目录名称，整理器不会按 EXIF 日期重新分配月份。
- 视频分流、截图候选识别，保留已有 `text`、`screenshot`、`video` 分类。
  截图名称和 PNG 分辨率只是启发式规则，可能误判。
- 可选 SHA-256 完全重复文件删除、AAE 编辑记录删除。两者都需要单独开关，执行还要加 `--apply`。
- OCR 筛出文字／聊天图片候选，再由人查看预览图并决定是否移动。
- 读取拍摄时间、GPS、相机等信息，按时间和地理范围搜索，导出地点观察和可能的跨国移动区间。
  详细用法见[元数据搜索](docs/metadata.zh-CN.md)。

## 安装

需要 Python 3.12 或更新版本。基本目录整理只使用 Python 标准库。

```sh
git clone https://github.com/lenkazuma/photo-library-organizer.git
cd photo-library-organizer
python -m venv .venv
# 激活虚拟环境后执行：
python -m pip install -e ".[metadata]"
# 可选：读取 HEIC/HEIF
python -m pip install -e ".[heif]"
# 可选：OCR 候选检测和预览图
python -m pip install -e ".[ocr]"
```

也可以直接运行 `python organize_photos.py --help`；其他脚本入口是
`python photo_metadata.py` 和 `python text_review.py`。

## 整理照片

第一次请在有备份的副本上试用，每次明确指定照片库目录。

```sh
# 只预览，不创建目录、写日志或改动照片：
photo-organize --root "E:\Photos" --defer-screenshots --lang zh

# 确认预览后执行移动：
photo-organize --root "E:\Photos" --defer-screenshots --apply --lang zh

# 按 docs/text-review.zh-CN.md 完成文字／聊天图片人工复核后，
# 再预览并整理剩余的新截图：
photo-organize --root "E:\Photos" --lang zh
photo-organize --root "E:\Photos" --apply --lang zh

# 可选清理：先查看计划，再自行加 --apply 执行：
photo-organize --root "E:\Photos" --deduplicate --remove-aae --lang zh
```

macOS/Linux 可改用 `--root "/path/to/photos"`。`Selfie` 顶层相册默认包含；
`--folder Holiday` 可额外包含其他顶层相册。未指定的其他相册、根目录散落文件和未知格式会保留。
移动后留下的空源目录也会保留。

执行时，新的 JSONL 操作日志写入照片库的 `.photo-organizer/logs/`。
也可用 `--log /private/path/new-audit.jsonl` 指定新文件。预览默认完全不写文件，
只有显式提供 `--log` 才保存预览。每次运行重新生成计划，执行前再次核查源文件。

## 根据照片地点辅助整理出入境线索

[中文使用指南](docs/metadata.zh-CN.md)提供建立索引、搜索和导出时间线的命令。
通过本地地点配置定义城市、机场等地理范围，GPS 不会发送给在线地图或地址查询服务。
示例地点范围只是演示，不是完整、准确的国界数据库。

照片只能提供拍摄地点和时间的观察证据。不同国家的相邻照片可以提示一段可能移动的时间区间，
不能证明准确过关日期、移民身份或连续居住。缺少 GPS、相机时间不准、缺少时区和照片空白期都会保留为限制。

## 保护措施与限制

- 移动采用排他创建目标、哈希校验，再删除已验证的源文件；不覆盖不同的同名照片。
  复制失败时保留源文件，可能留下需人工检查的不完整目标文件。暂不支持自动回滚。
- 同一照片库一次只运行一个任务，执行时暂停其他写入／同步变更。
  这是个人工具，不是事务文件系统，也不保证抵抗恶意并发路径变更。
- 跳过符号链接、目录联接、隐藏／下划线目录和 `[FORMAT]`。
- 去重是永久删除，需保留备份；只删除字节完全相同的文件，不删除“看起来相似”的照片。
  AAE 删除会丢失 Apple 编辑指令，但不删除原图。
- OCR 候选必须人工复核，稀疏聊天截图也可能属于文字类，见[文字图片复核](docs/text-review.zh-CN.md)。
- 索引、预览图和操作日志包含私人路径／位置等信息，应保存在仓库之外；忽略规则不能替代上传前检查。

## 测试与后续事项

```sh
python -m unittest discover -s tests -v
```

测试只使用临时目录和虚构文件，不扫描实际照片库。
已完成的发布准备和仍待改进的部分列在[中英文发布清单与路线图](docs/ROADMAP.md)。MIT 许可证。
