# Branch A Contract: Reading EPUB/PDF

本文件固化分支 A 的当前工程契约，避免后续在链接、章节 id、脚注和人工修正上反复摇摆。

## Scope

分支 A 负责阅读交付：

- 外文书按章翻译。
- 输出命名 EPUB/PDF。
- 保留图、表、封面和必要附属内容。
- 支持译后 EPUB 内部链接的最低可用性。
- 未来支持用户看样后的定点修正。

分支 A 不负责知识网络化；知识分支只复用分支 A 产生的 BookIR、章节 id、原文/译文和来源追溯。

## Chapter ID Contract

每个 BookIR chapter 必须有稳定字段：

- `index`: 当前输出顺序，1-based。
- `title`: 章节标题。
- `chapter_id`: `ch-{index:03d}-{slug}`。
- `source_pages`: PDF 页码或 EPUB spine 序号。
- `source_internal_path`: EPUB 源 spine XHTML 路径；PDF 可为空。

规则：

- `chapter_id` 由章节顺序和标题 slug 生成。
- 同一次 rebuild 中，`chapter_id` 是翻译、EPUB 渲染、polish、后续知识分支和未来定点修正的共同锚点。
- 章节重切分后允许 `chapter_id` 改变；因此任何跨版本修正记录必须同时保存源文件 hash / book.json hash。
- 不从 Markdown 标题临时猜章节 id。

## EPUB Link Contract

当前实现层级是 L2。

| Level | 承诺 | 当前状态 |
|---|---|---|
| L0 | 外链、mailto 保留 | 已支持 |
| L1 | ingest 保留 Markdown 链接文本和 href | 已支持 |
| L2 | 源 EPUB spine 文件路径重写到输出章节 XHTML | 已支持 |
| L3 | fragment / footnote / endnote 精确双向锚点 | 暂不承诺 |
| L4 | PDF 内链 | 暂不承诺 |

L2 规则：

- 源链接 `OEBPS/chapter2.xhtml#note-3` 如果能匹配到输出章节，则重写为 `002-chapter-title.xhtml`。
- 当前不会保留 `#note-3`，因为 ingest 阶段没有稳定保存源 fragment id 到译后 XHTML id 的映射。
- 这样做牺牲精确锚点，但避免生成坏链接。
- `validate_epub_internal_hrefs()` 必须能统计输出 EPUB 中内部链接的可解析率。

L3 进入条件：

- ingest 能保存源 `id` / `href fragment`。
- BookIR 能表达 footnote/endnote objects。
- 渲染 EPUB 能生成稳定 `id`。
- 有自动测试覆盖双向脚注和跨章节 fragment。

## Footnote / Endnote Contract

当前策略：

- PDF 页下注脚：保留在对应页/章节上下文，不全局合并。
- EPUB 章末连续编号注释：只做视觉压缩为 `chapter-notes` 区块，不改变语义链接。
- 书末 Notes / References / Index：作为附属内容保留，不进入主叙事翻译；PDF 中可用原页图像保真。

禁止：

- 不因几个脚注样本继续堆正则。
- 不把页下注脚和书末尾注混成同一类对象。
- 不在没有 fragment 映射时伪造脚注双向链接。

## PDF Internal Link Boundary

PDF 内链属于 L4，当前只做评估，不承诺实现。

评估条件：

- Docling 或替代引擎能否导出 PDF link annotation。
- link target 是页码、坐标、outline destination 还是 named destination。
- 是否能映射到 BookIR chapter/page/object。

如果导出能力不足，PDF 内链不进入分支 A 的近期实现。

## Targeted Correction TODO

未来命令形态建议：

```bash
pdf-translator patch RUN_DIR --chapter-id ch-004-introduction --selector paragraph:12 --instruction "..."
```

修正记录必须保存：

- `chapter_id`
- selector：段落 / 行号 / 文本 hash / EPUB CFI 或其他定位方式
- 原文片段 hash
- 旧译文
- 新译文
- 操作时间和模型/人工来源

最小重渲染策略：

- 更新 `translated.md` 中对应片段。
- 更新对应 translated chapter。
- 重渲染 EPUB。
- 不重新翻译整本书。

实现前置：

- 章节 id 稳定。
- semantic selector 稳定。
- `translated.md` 与章节数组可双向定位。
