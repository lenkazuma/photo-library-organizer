# 离线照片元数据搜索

[English](metadata.md) · [项目说明](../README.zh-CN.md)

在本地建立 EXIF 索引，按地点或日期找照片，再导出地点观察时间线。它可以帮助你根据自己拥有的照片回顾旅行经过。**它不能生成官方出入境记录，也不能确定准确过境日期。**

程序不会向外部服务发送照片或坐标。建立索引只读取照片，不移动、修改或删除原图；搜索和时间线功能只读取本地索引，不再打开原始照片。

## 安装

需要 Python 3.12 或以上：

```sh
python -m pip install ".[metadata]"
```

需要读取 HEIC/HEIF 时，安装可选解码器：

```sh
python -m pip install ".[heif]"
```

以下命令在项目源代码目录中运行。安装后也可使用同参数的 `photo-metadata` 命令。只有 `index` 需要 Pillow；`search` 和 `timeline` 只需要 Python 标准库。

## 1. 配置本地地点区域

将 `examples/places.example.json` 复制到私人本地文件，例如 `private-data/places.json`，按自己的搜索需要调整矩形范围：

```json
{
  "places": [
    {
      "id": "sydney-example",
      "name": "Sydney example region",
      "name_zh": "悉尼示例区域",
      "country": "AU",
      "bbox": [150.5, -34.3, 151.5, -33.3]
    }
  ]
}
```

`bbox` 顺序为 `[西经度, 南纬度, 东经度, 北纬度]`，使用十进制度数，包含四边边界。当西经度大于东经度时，表示跨越 180 度经线的矩形。`id` 必须唯一；`id`、`name`、`country`、`bbox` 必填，`name_zh` 可选。

仓库中的悉尼、墨尔本、深圳范围只是演示用的城市矩形，**不覆盖整个国家，也不代表权威行政区或国界**。`country` 是用户定义的分组标签，前后一致即可。照片坐标不在任何已配置矩形内时，地点保持未知。本功能没有在线地址查询、全球国家识别或无 GPS 照片的位置猜测。

多个区域重叠时，选择球面面积最小的矩形；面积相同按 ID 排序。索引同时保留所有匹配区域的 ID 和国家标签。若它们的国家标签冲突，照片仍可搜索，但时间线不会用该照片推断国家变化。

## 2. 建立索引

```sh
python photo_metadata.py index "D:/MyPhotos" --places private-data/places.json --output private-data/index.jsonl
```

`D:/MyPhotos` 是占位路径，请替换为自己的照片目录。`--places` 可省略；省略时仍能按 GPS 半径搜索，但不会自动获得地点或国家标签。

支持 JPG、JPEG、PNG、TIFF/TIF、WebP、HEIC、HEIF 扩展名。具体能否读取，取决于解码器和图片是否包含 EXIF 信息。视频、RAW、XMP 附加文件、图片内容识别和 OCR 不属于此元数据功能。

扫描跳过符号链接和 Windows junction。始终跳过 `.git`；默认还跳过隐藏文件、隐藏目录，以及名称以 `_` 开头的目录。`--include-hidden` 可以包含这些项目，但仍不扫描 `.git`。根目录本身若为符号链接或 junction，也会被拒绝。

索引是 JSONL，每张发现的图片占一行，包含：

- 相对照片根目录的路径、文件名、扩展名和像素尺寸。
- 相机品牌与型号（存在时）。
- 有效的 EXIF 原始拍摄时间 `DateTimeOriginal` 及其 `OffsetTimeOriginal` 时区偏移。
- GPS 经纬度、可选高度和 GPS UTC 时间。
- 选中的本地区域、全部匹配区域 ID／国家标签及选择规则。
- 时间来源 `capture_time_source`、时区状态 `timezone_status`，以及明确的 `missing`、`warnings`、`errors` 字段。

若原始拍摄时间缺失或无效，可使用有效的 `GPSDateStamp` 加 `GPSTimeStamp`，并明确标记不同时间来源。GPS 时间可能是定位时间，并不保证就是按下快门的瞬间。EXIF 修改时间不会当作拍摄时间。原始时钟有效但偏移字段损坏时，会保留无时区的本地时间并记录警告；不会拿 GPS 时间替原始时钟猜测时区。

**默认不以文件修改时间代替拍摄时间。** 只有显式添加 `--mtime-fallback`，才会在缺少拍摄元数据时使用 UTC 文件修改时间，来源标记为 `filesystem:mtime_fallback` 并附警告。这不能证明拍摄日期；生成时间线时默认排除，除非另加 `--include-mtime`。

损坏或无法解码的图片仍会保留在索引中并记录错误。最终统计输出到标准错误流；有图片读取错误或目录扫描错误时退出码为 `2`，此时也可能已成功写出部分索引。单纯缺少 EXIF 不算读取失败。判断索引是否完整前，请查看结束统计。

## 3. 搜索照片

多个条件组合时，照片必须同时满足所有条件：

```sh
# 按照片当地日历日期搜索，包含首尾两天
python photo_metadata.py search private-data/index.jsonl --after 2024-01-01 --before 2024-12-31

# 搜索悉尼示例坐标周边 25 公里
python photo_metadata.py search private-data/index.jsonl --near -33.86 151.21 25

# 用户定义的国家／地点标签、相机和文件名
python photo_metadata.py search private-data/index.jsonl --country AU --place 悉尼 --camera Example --filename holiday

# 导出中文表头及地点名称
python photo_metadata.py search private-data/index.jsonl --country AU --format csv --lang zh --output private-data/au-photos.csv
```

`--near 纬度 经度 半径公里` 使用球面大圆距离。经纬度必须在合法范围，半径必须为有限非负数。`--country` 匹配完整标签，不区分大小写；`--place` 查找选中区域的 ID、中英文名称；相机、文件名也支持忽略大小写的子字符串匹配。不会搜索重叠但未选中区域的名称。

只填写日期时，按照片记载的当地日历日期比较。也可以填写完整时间，如 `2024-03-01T00:00:00+11:00`。有时区偏移的照片只能与同样带偏移的时间边界按实际时刻比较；未知时区的照片只能与同样没有偏移的时间边界比较。不兼容的记录会跳过并计数，程序不会套用电脑时区。跨多种时区且只关心日历日期时，使用纯日期筛选即可。

JSON 输出保留匹配记录的全部索引字段、结果数和跳过统计。搜索 CSV 输出主要字段；详细错误及重叠区域信息保留在 JSON 中。没有匹配结果不算失败。搜索结果顺序沿用索引顺序，不按拍摄时间或距离重新排列。

## 4. 导出旅行观察时间线

```sh
python photo_metadata.py timeline private-data/index.jsonl --format csv --lang en --output private-data/travel-en.csv
python photo_metadata.py timeline private-data/index.jsonl --format csv --lang zh --output private-data/travel-zh.csv
python photo_metadata.py timeline private-data/index.jsonl --output private-data/travel.json
```

报告包含有时间信息的照片观察，以及相邻可用国家观察之间的可能移动区间。同一国家连续出现时，会更新该国最近一次观察。其间没有国家标签或存在区域冲突的照片会计数，但不会被猜测归属。元数据警告和错误保留在 JSON 及时间线 CSV 中；缺失或无效时间的照片会计数，并从有序观察序列中省略。

例如，3 月 1 日有一张标记为 `AU` 的照片，下一次可用国家观察是 3 月 5 日的 `CN` 照片，只能提示**两次观察之间可能发生过移动**，不能断言出境或入境日期是 3 月 1 日或 3 月 5 日。缺少照片可能掩盖中间国家、多次往返、中转或返回；位置元数据也可能缺失、被修改、不准确，或来自别人分享的照片。照片本身不能证明某个人当时在场。

所有纳入记录都有时区偏移时，按 UTC 实际时刻排序；只要有记录缺少时区，就按记载的当地拍摄时间排序，并警告跨时区先后顺序可能不准确。程序不会根据坐标猜时区。可排序时间相同、国家标签不同的观察会标记为同时或冲突观察；重叠区域的国家标签冲突则直接不参与移动推断。核对旅行经过时，应结合原始元数据和其他独立记录。

## 输出与隐私

- JSON、JSONL 使用 UTF-8；保存的 CSV 含 UTF-8 BOM，方便电子表格软件读取。`--lang en`／`--lang zh` 控制说明、CSV 表头和可用的地点名称；JSON 字段名、状态码保持英文，便于程序读取。
- 索引使用 `.jsonl`；JSON 报告使用 `.json`；CSV 需指定 `--format csv` 并使用 `.csv`。扩展名必须与格式一致。
- 默认不覆盖已有输出，显式添加 `--overwrite` 才会替换。搜索／时间线不能覆盖输入索引；输出路径及其上级目录不能是符号链接或 junction；不接受媒体文件扩展名作为输出。
- 类似电子表格公式的 CSV 字符串会转义，正常的经纬度数值保留为数字。
- 实际照片、索引、CSV、真实地点配置和旅行记录应保留在私人目录，不提交到公开 Git 历史。即使没有上传照片，索引仍可能包含精确位置、时间和文件名。仓库示例区域及测试不含个人观察记录。
- 目前是本地命令行 MVP：没有地图界面、增量数据库、人脸／内容搜索、机场识别、自动全球国界数据或官方出入境系统对接。修改区域配置后需要重新建立索引。

## 不使用私人数据的测试

```sh
python -m unittest discover -s tests -p test_metadata.py -v
```

测试在临时目录中生成小图片和虚构记录，检查 EXIF/GPS 读取、区域重叠、距离／日期筛选、时区不确定性、输出保护，以及建立索引 → 搜索 → 时间线的完整命令行流程。未安装 Pillow 时会跳过图片读取测试，其余测试只使用标准库。
