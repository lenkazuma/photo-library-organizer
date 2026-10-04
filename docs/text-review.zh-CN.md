# 本地文字图片人工复核

[English](text-review.md)

这个可选流程帮助把聊天截图、笔记、文章等以文字为主的图片从普通照片中分出。文字区域检测只生成候选清单；你逐张查看后明确填写 `text` 或 `keep`。工具不会根据分数自动移动图片。

## 适用范围与依赖

仅扫描有效 `YYYYMM` 文件夹根层的图片。已有的 `text`、`screenshot`、`video` 分类，嵌套目录，带后缀的月份目录，以及符号链接／目录联接均被排除。先用主整理器规范月份目录；希望聊天截图优先归入文字分类时，先启用主整理器的延后截图分类选项，完成本流程后再处理其余截图。

命令帮助、预览、决策校验和合成测试仅依赖 Python 标准库。真正执行图片检测时还需要：

```shell
python -m pip install Pillow numpy opencv-python-headless rapidocr-onnxruntime
# 仅处理 HEIC/HEIF 时额外安装：
python -m pip install pillow-heif
```

图片在本地处理。检测使用文字框与图像统计，不保存识别出的聊天正文。图片解码器的安全限制保持启用。动画和多页图片仅评估首帧／第一页。大规模使用前，应先用自己选定的非敏感样本检查可选 OCR 环境兼容性和分类效果。

## 扫描、查看、填写决策、预览、应用

每条命令都必须明确提供照片根目录。以下仅使用虚构示例路径：

```shell
python text_review.py scan --root "D:/ExamplePhotos"
python text_review.py preview --root "D:/ExamplePhotos"
```

在本机打开 `D:/ExamplePhotos/.photo-organizer/text-review/review.html`。每张卡片显示 ID，点击图片可以进一步查看。如果浏览器无法显示 HEIC/HEIF 等格式，请用本机图片查看器打开。该 HTML 是引用原始图片的本地复核页面，不是在线服务，也不会自动填写判断。

将同目录的 `decisions.template.json` 复制为 `decisions.json`，保留其中的 `format`、`root`、`scan_id`。逐一检查候选图后，把 `decisions` 对象中的每个 `null` 改成 `"text"` 或 `"keep"`。例如，内部决策部分可能是：

```json
{"0": "text", "1": "keep"}
```

上面只是内部映射示例；实际文件必须保留生成时的完整外层信息。每个候选 ID 都必须有明确决定。重新扫描会生成新的扫描 ID，旧决策不能静默套用到新的候选清单。

```shell
# 仅预览，不移动原图，也不更新之前的人工复核记录。
python text_review.py apply --root "D:/ExamplePhotos" --decisions "D:/ExamplePhotos/.photo-organizer/text-review/decisions.json"
# 检查预览日志后，明确加入 --apply 执行移动。
python text_review.py apply --root "D:/ExamplePhotos" --decisions "D:/ExamplePhotos/.photo-organizer/text-review/decisions.json" --apply
# 检查剩余候选和保留的错误。
python text_review.py scan --root "D:/ExamplePhotos"
```

只有 `apply` 这个子命令仍然是预览，必须额外加上 **`--apply`** 才会移动文件。没有依据历史候选名单直接应用、或“排除名单之外全部移动”的旧入口。

## 什么图片适合归入 text？

- 能辨识为聊天对话的图片，即使文字稀疏、带头像／表情／大片留白，也可归入文字分类。
- 文字是主体的笔记、语录、文章、菜谱、幻灯片、文档。
- 带路牌、短标题或水印的普通照片通常保留。不确定时选 `keep`。

检测器可能漏掉文字，也可能把普通照片列为候选。`scan --include-all` 可以把所有成功解码、尚未人工复核的图片都放入候选，忽略分数门槛。若也要重新检查过去已作决定的图片，再加 `--reconsider-all`。这些选项不代表能够自动识别所有聊天图。图片中的内容只是待分类的数据，不应作为需要执行的指令。

## 缓存、隐私与异常恢复

所有命令均支持 `--state-dir "D:/PrivateReviewState"`。默认位置是 `ROOT/.photo-organizer`，具体文件放在其 `text-review` 子目录中；同一轮复核应始终使用相同设置。状态绑定到一个规范化根目录和明确的格式版本。本工具不读取、不迁移、不复用原始个人脚本的旧缓存、排除名单、日志或照片。

检测结果按相对路径、文件大小、纳秒级修改时间缓存，图片变化后重新检测。`--reconsider-all` 忽略过去的人工判断，但继续复用未变化图片的检测结果。这种指纹只用于增量处理，不等于内容完整性证明；实际移动还会核对 SHA-256。

执行前先校验整批源文件。选为 `text` 的图片进入当月 `text` 文件夹；同名文件使用编号后缀，不覆盖已有文件。移动过程是独占创建目标文件、复制、核对 SHA-256，再删除原位置文件；日志记录计划、复制校验和最终完成状态。校验失败时保留源文件，目标位置可能留下部分文件或完整副本，供人工检查。遇到失败不要盲目重跑，应先核查日志与相关路径，再重新扫描。请保留备份，应用过程中不要同时编辑照片库或另开扫描。

无法读取的文件原地保留，错误写入 `errors.json`。缓存、错误报告、决策文件、审计日志和 HTML 预览可能包含私人文件名、路径或可见图像；整个状态目录都应保持私有并排除在 Git 之外，不要将真实照片库作为示例数据上传。

## 当前限制与待完善事项

- 仅复核 `YYYYMM` 根层图片，与递归照片元数据搜索是独立功能。
- 检测采用启发式规则，需要人工复核；尚无准确率／召回率评估结论。
- HTML 预览直接引用本地原图；暂未提供编辑决策的界面、浏览器 HEIC 解码或联系表导出。
- 稳定发布前可补充允许再分发的标注评估集、打包后的可选 OCR 依赖，以及更多系统和 HEIF 格式验证。
