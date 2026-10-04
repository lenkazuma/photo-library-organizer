# Release checklist and roadmap / 发布清单与后续事项

This is a personal code archive, not a managed service or a promise of ongoing support.
这是个人代码归档，不是托管服务，也不承诺持续维护。

## Included in the first release / 首次发布已整理

- Explicit library path; no personal source paths. / 显式指定照片库，不含私人硬编码路径。
- Default preview; opt-in deletion; no-overwrite verified moves and journals. / 默认预览、删除需单独开启、移动校验与日志。
- Separate reusable OCR detection and reviewed apply workflow. / 可复用 OCR 检测与人工复核后执行的流程。
- Offline EXIF/GPS search and location observation timelines. / 离线 EXIF/GPS 搜索及地点观察时间线。
- English/Chinese documentation, optional dependencies and MIT license. / 中英文文档、可选依赖与 MIT 许可证。
- Synthetic tests and Windows/Linux CI configuration. / 虚构数据测试及 Windows/Linux 自动测试配置。
- Ignore rules for images, indexes, review state, secrets and reports. / 忽略照片、索引、复核缓存、密钥及报告。

## Limitations and possible improvements / 限制与可选改进

- Automatic country-boundary mapping is not implemented. Region boxes are user-defined,
  can overlap and are not a global border dataset. / 暂无自动国界识别，用户定义的区域可能重叠，不是全球国界数据。
- GPS points alone cannot prove entry/exit dates. Add independent records manually if
  needed. / GPS 照片不能证明出入境日期，需要时自行结合其他记录。
- Broader video/RAW metadata support and optional ExifTool integration. / 待扩展视频／RAW 元数据和可选 ExifTool 集成。
- Incremental metadata indexing for very large libraries. / 待扩展超大照片库增量元数据索引。
- A graphical review interface and configurable screenshot heuristics. / 可考虑图形复核界面和可配置截图规则。
- Automatic undo/recovery is not implemented. Audit logs support manual inspection,
  not guaranteed rollback. / 暂无自动撤销；日志便于人工核查，不保证回滚。
- Existing empty source folders are retained. / 目前保留整理后留下的空源目录。
- Optional OCR/HEIF dependency combinations need more cross-platform field testing.
  / 可选 OCR／HEIF 依赖组合还需要更多跨平台实际测试。

## Before publishing your own fork / 发布你自己的分支之前

1. Review the exact staged files and repository history. / 检查实际暂存文件及仓库历史。
2. Use only synthetic or deliberately public samples. / 仅使用虚构或明确可公开的示例。
3. Exclude actual media, GPS indexes, contact sheets, decisions, audits and credentials.
   / 排除实际照片、GPS 索引、预览图、复核记录、日志及凭据。
4. Run the offline tests and review limitations against your changes. / 运行离线测试并同步更新限制说明。
