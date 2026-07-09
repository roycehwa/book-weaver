# Phase A 优化计划（基于《Land and trade in early Islam》回溯分析）

> 日期：2026-07-07  
> 触发样本：job `58c7dfb0d0c542c5aa6e8183aaaf85d3`，400 chunks，0 retries，但 events 至少 3 次 fail+resume，最终靠 Codex 介入才跑完。  
> 目标：把这 4 件事一次性收敛，避免再次手动救火。

## 0. 现状与根因（数据驱动）

### 0.1 翻译 worker 不稳定
- 现象：单次翻译 retry=0（chunk 级都成功），但**整段 worker 死掉**导致 3 次 `job_failed`+`job_resumed`。
- 根因：
  - worker 进程寄生在 uvicorn 里（`translation-worker.lock` 持有 PID 44566，正是后端进程）。
  - uvicorn 重启 / OOM / socket 断开 → worker 跟着死。
  - 没有任何"心跳监控 + 自动 resume"机制，只能等外部触发。
- 期望：worker 与 API 解耦；supervisor 看门狗能自动恢复。

### 0.2 术语门槛过低
- 数字：`candidates.json: 172`，`active.json: 136`（人审后）。
- 第一条样本：`"Land and Trade"`（occurrences=162, chapter_count=16, score=13.0, confidence=0.929）被当术语，其实就是**书名/章名级别专名**。
- 根因：policy 中"两词人名不降权"+ 高 score 阈值偏低，导致任何高频两词短语都入选。
- 期望：保留真正的人名/地名/事件/作品专名；剔除通用词组与书名章名。

### 0.3 组件翻译规则未落实
- `render_policy` 写了 `apparatus: references_bibliography_index_sections_classified_separately`，但：
  - `ch-006-notes-on-transcription-and-dates` 的 `translate=True`（应 False）
  - chapters **没有 `kind` 字段**（apparatus/toc/index/cover/narrative），下游无法按类型过滤
  - 615 pages 的 page 记录里只有 `table_count`，**没有 block-level `kind: table/figure/note`**，所以正文页表格无法识别跳过
- 直接后果：
  - 目录、附录被翻译
  - 正文中的表格被翻译
  - 注脚未翻译（因为我们设了"人名/书名不译"）→ 又被审阅判 `mixed_english`

### 0.4 审阅规范缺豁免源
- `pre_review.issue_counts`：`mixed_english: 69`（占 96 个 flagged 中的 72%）
- 触发源：注脚里保留的人名/书名/引文/拉丁文。
- 期望：把"已知合法英文源"写入豁免白名单，审阅只关心**真正未翻译的正文**。

### 0.5 首页布局与 Phase A 不符
- 现状：首页是"工作台/深度阅读/审阅"等与 Phase A 不匹配的入口。
- 期望：首页 = 两大模块入口
  1. **书籍处理转换**（intake / chapters / 格式转换 / 章节分析）
  2. **书籍翻译**（术语 / 章节 / 翻译 / 审阅）
- Phase B 入口做占位。

---

## 1. 总体改造方案

按 4 件事分层推进，**先做不破坏现有数据的层**：

| 优先级 | 范围 | 文档 | 实施位置 |
|---|---|---|---|
| P0 | 翻译 supervisor | §2 | `scripts/translation_supervisor.py` + 后端解耦 |
| P1 | 组件分类 + 翻译开关 | §3 | `src/pdf_translator/book_ir.py` + `reconstruct.py` + `translate.py` |
| P2 | 术语门槛 | §4 | `src/pdf_translator/glossary/extract.py` + policy |
| P2 | 审阅豁免白名单 | §5 | `src/pdf_translator/review/rules.py` |
| P3 | 首页重构 | §6 | `frontend/src/components/Home.tsx` + `Layout.tsx` |

P0 单独先行（高风险、最影响稳定性）。  
P1+P2 一起做（数据 schema 改造是同一层）。  
P3 独立做（前端单边）。

---

## 2. P0：翻译进程保护器（独立 supervisor）

### 2.1 目标
- worker 不再寄生在 uvicorn。
- supervisor 独立进程，每 30s 扫一次 `state=translating` 的 job。
- 检测到"卡死"（`updated_at` 距今 > 90s 且无新 cache 文件）→ 自动 `service.resume(job_id)`。
- supervisor 自己挂掉也不影响 API（解耦）。

### 2.2 架构
```
[uvicorn backend]                  [translation supervisor]   [job storage]
        |                                  |                        |
        |-- POST /jobs/{id}/resume ------->|---- scan jobs -------->|
        |                                  |    detect stuck        |
        |                                  |    POST resume (HTTP)  |
        |                                  |<-----------------------|
        |<-- 202 ----------------------------|                        |
```

### 2.3 实施步骤
1. **新增 `scripts/translation_supervisor.py`**（独立 CLI，~150 行）：
   - 启动参数：`--backend-url http://127.0.0.1:8000`、`--scan-interval 30`、`--stuck-threshold 90`、`--max-resume-per-hour 6`
   - 逻辑：每轮调用 `GET /api/jobs?state=translating` → 对每个 job 读 `updated_at` + `cache/translate` mtime → 触发条件满足就 `POST /api/jobs/{id}/resume`
   - 自带日志和指标（启动时间、扫描次数、resume 次数、当前在跑 job 列表）
2. **把 worker 从 uvicorn 拆出来**：
   - 当前：worker 由后端 lifespan 启，pid 写入 `translation-worker.lock`
   - 改为：worker 由 supervisor 进程持有；后端只接受"启动翻译 / 恢复翻译"的 HTTP 请求，**不直接 spawn worker**
   - 改 `backend/main.py:572` `_run_job_in_background`；`backend/job_service.py:156` `execute`；`backend/job_service.py:164` `resume`
3. **心跳 + 卡死检测**：
   - worker 每 5s 写一次 `cache/translate/.heartbeat`（mtime 即心跳）
   - supervisor 比对 `updated_at` 与 `.heartbeat` mtime 决定是否卡死
4. **chunk-level 重试**（在 `translate.py`）：
   - 单 chunk 失败 3 次 → 退避后整批重试（不再是"一个慢 chunk 拖死整轮"）
5. **SSE 进度推送**：
   - 后端新增 `GET /api/jobs/{id}/events/stream` (SSE)
   - supervisor 每次 resume 推一条 `supervisor_resumed` 事件，前端能看到
6. **deploy**：
   - `deploy-local.sh` 增加 supervisor 启动步骤
   - 文档：`docs/TRANSLATION_SUPERVISOR.md`

### 2.4 验证标准
- 关掉 uvicorn，supervisor 继续运行 → 重启 uvicorn 后翻译自动续上
- 模拟 worker 死（kill -9 worker 进程）→ 90s 内 supervisor 自动 resume
- 跑一遍 `Land and trade` 整书：零 Codex 介入、零手动 resume

---

## 3. P1：组件分类 + 翻译开关

### 3.1 BookIR schema 改造

`book.json` 中每个 chapter 新增 **`kind` 字段**（枚举）：
- `cover` — 封面
- `toc` — 目录
- `front_matter` — 前言、致谢、缩写表等
- `narrative` — 正文叙事（默认）
- `apparatus` — 注脚/凡例/译名表/缩写
- `bibliography` — 参考书目
- `index` — 索引
- `appendix` — 附录

**每章新增 `translate: bool`**（覆盖 `render_policy` 的默认规则）。

`book.json` 中 `pages[]` 每页保留 `blocks[]`，**每个 block 显式带 `kind`**：
- `text` / `table` / `figure` / `caption` / `note` / `heading` / `list`

### 3.2 翻译时跳过规则
`translate.py` 在拼 `translation-input.md` 时：
- chapter.kind ∈ {`cover`, `toc`, `apparatus`, `bibliography`, `index`, `appendix`} → 整章 `translate=False`
- block.kind ∈ {`table`, `figure`} → 即使在 narrative 章内也整块不送模型
- 仅 `text` / `caption` / `heading` / `list` 送翻译；`caption` 视上下文决定是否译

### 3.3 实施步骤
1. **`src/pdf_translator/book_ir.py`**：定义 `ChapterKind` / `BlockKind` 枚举，写 `classify_chapter()` 启发式（标题匹配 + 页范围 + metadata）
2. **`src/pdf_translator/reconstruct.py`**：解析时给每个 chapter 与 block 打 `kind`
3. **`src/pdf_translator/translate.py`**：拼 `translation-input.md` 时按 kind 过滤
4. **回归测试** `tests/test_book_rebuild.py` + 新增 `tests/test_chapter_kind.py`：
   - 验证 `Notes on Transcription` 分类为 `apparatus`，`translate=False`
   - 验证正文中 `block.kind=='table'` 的 block 不出现在 `translation-input.md`

---

## 4. P2：术语门槛

### 4.1 现状 policy 数值
`extraction-policy.json` 显示：
- `raw_phrases_seen: 1774`
- `eligible: 172`
- `max_candidates: 200`

实际只有 172 条进 candidates，但 active 还有 136 条。问题在 **eligibility 过滤**。

### 4.2 新阈值（在 `extraction-policy.json` v3 写明）
| 维度 | 旧 | 新 |
|---|---|---|
| 最小 occurrences | 3 | 8 |
| 最小 chapter_count | 2 | 4 |
| 候选上限 | 200 | 60 |
| 通用词黑名单（top 200 English function words + 学术常用词组） | 无 | 加入 |
| 标题/章名/书名匹配 | 不算分 | 负权重（-5）|
| 词典单词数（heuristic） | 不限制 | ≤1 个 token 强制 reject |

### 4.3 实施步骤（已回滚，见 §10.2）

> **2026-07-07 修订**：原计划的硬上限和硬阈值已被回滚。固定
> `MIN_OCCURRENCES=8 / MIN_CHAPTERS=4 / MAX_CANDIDATES=60` 与书的体量
> 无关，100 页儿童书和 600 页学术专著共用同一组数是不合理的。
> Phase A 现在只保留通用词黑名单与扩展 metadata_exclusions。

1. `src/pdf_translator/glossary_thresholds.py`：保留作为常量模块，
   但 `extract_glossary_candidates` 不再硬性套用，调用方可显式
   传入 `max_candidates` 覆盖。
2. `src/pdf_translator/glossary_extraction.py._metadata_exclusions`：
   保留扩展（取所有 chapter 标题而非前 3 个），这一步是合理的。
3. `src/pdf_translator/glossary.py`：`is_generic_stop_phrase` 仍会
   过滤通用词短语（如 "eighth century"、"land and trade"），但不再
   套用 `MIN_*` 硬门槛。
4. 真正能改进术语提取质量的方向是**重写提取算法**（TF-IDF / 互信息
   / 频次分布曲线自动 elbow），而不是手拍门槛数。后续单独规划。

---

## 5. P2：审阅豁免白名单

### 5.1 目标
`mixed_english` 误报从 69 → 目标 < 20。

### 5.2 豁免源
在 `src/pdf_translator/review/rules.py` 新增 `EXEMPTION_RULES`：
1. **注脚段**：`segment_id` 含 `:note` 或 chapter.kind=apparatus → 豁免 `mixed_english`
2. **引文段**：`> ...` 块引用 → 豁免
3. **人名/书名**：与 `glossary/active.json` 中 source 匹配的英文段 → 豁免
4. **拉丁文 / 阿拉伯文**（langdetect 命中 la|ar）→ 豁免
5. **代码段**（inline code + code block）→ 豁免

### 5.3 实施步骤
1. `src/pdf_translator/review/rules.py` 引入 `EXEMPTION_RULES` 与 `apply_exemptions(items, glossary_active)`
2. `pre_review.json` schema 加 `exemption_counts` 字段
3. `tests/test_review.py` + 新增 `tests/test_review_exemptions.py`
4. 文档：`docs/REVIEW_EXEMPTIONS.md`

---

## 6. P3：首页重构

### 6.1 目标布局（最终态）

```
┌─────────────────────────────────────────────┐
│  BookWeaver · Phase A                       │
├─────────────────────────────────────────────┤
│  📚 书籍处理转换                              │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐         │
│  │ Intake  │ │ Chapters│ │ Convert │         │
│  │ PDF/EPUB│ │ 章节分析 │ │ 格式转换 │         │
│  └─────────┘ └─────────┘ └─────────┘         │
│                                             │
│  🌐 书籍翻译                                  │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐         │
│  │ Glossary│ │ Translate│ │ Review │         │
│  │  术语   │ │  章节翻译│ │  审阅  │         │
│  └─────────┘ └─────────┘ └─────────┘         │
│                                             │
│  Phase B 知识解析 (即将推出)                  │
└─────────────────────────────────────────────┘
```

### 6.2 实施步骤
1. `frontend/src/components/Layout.tsx`：侧边栏只保留 2 个主导航 + 1 个禁用项
2. `frontend/src/components/Home.tsx`：按 6.1 草图重写
3. 删除/重命名现有不存在的"深度阅读""工作台"导航项
4. 路由保持向后兼容（旧的 `/review/*` 路径仍可访问，但不再展示在首页）

### 6.3 不做
- 不重写已有 Review 工作流代码
- 不删除旧路由（用户书签兼容）

---

## 7. 风险与回退

| 风险 | 缓解 |
|---|---|
| BookIR schema 改动破坏旧 job 兼容性 | chapter.kind 缺省=`narrative`；block.kind 缺省=`text`；旧 job 行为不变 |
| supervisor 启动失败 | `deploy-local.sh` 启动失败不阻塞主流程；supervisor 缺位时回到旧行为（Codex 介入）|
| glossary policy 升级影响已有人审 active 词表 | 只改 candidates 生成；active 不动；新 job 走新规则 |
| 审阅豁免过宽导致真正问题被掩盖 | `EXEMPTION_RULES` 每条写明豁免条件；测试覆盖反向用例（"正文里真出现英文人名应被标记"） |
| 前端改首页破坏用户现有书签 | 保留旧路由可达 |

---

## 8. 验证清单

完成后跑以下回归：

1. `pytest tests/test_book_rebuild.py tests/test_chapter_kind.py -v` — 章节分类
2. `pytest tests/test_glossary_extraction.py -v` — 术语候选 < 60
3. `pytest tests/test_review.py tests/test_review_exemptions.py -v` — 豁免规则
4. `book-weaver profile /tmp/land-and-trade.pdf --profile book` — 跑 profile 不报错
5. 手动集成测试：
   - 启动 supervisor + uvicorn
   - `book-weaver intake` 一本新 PDF
   - `book-weaver translate --target-lang zh-CN`
   - 监控 supervisor 日志应见 ≥1 轮扫描
   - 模拟 `kill -9 <worker pid>` → 90s 内 supervisor 应自动 resume
6. 前端：浏览器打开 `http://127.0.0.1:5173/` → 自动进入 Phase A
   上传/工作区入口，不出现空白首页

---

## 9. 实施顺序（推荐）

```
Week 1: P0 supervisor（独立、低风险、立竿见影）
Week 1-2: P1 组件分类（schema 改造 + translate.py 过滤）
Week 2: P2 术语门槛 + 审阅豁免（一前一后）
Week 2-3: P3 首页重构（独立、可并行）
```

每项完成后：
- 更新本文件 `## 10. 实施记录` 节
- 在 `docs/CHANGELOG.md` 写 changelog
- 在原 Phase A 文档中链接到本计划


## 10. 实施记录

### 2026-07-07 — P0 翻译 supervisor（独立进程 + SSE + 保险丝）

- 新增 `scripts/translation_supervisor.py`（296 行）：独立 CLI 进程，
  每 30s 扫一次 `state=translating` job，根据 `translation_activity.status`
  + `seconds_since_update` 判定卡死，自动 POST `/api/jobs/{id}/resume`。
- 新增 dry-run / metrics / rate limit / signal-aware shutdown。
- `backend/translation_supervisor.py` 改为默认开启的"保险丝"，uvicorn
  重启时也能兜底；可显式用 `BOOKMATE_DISABLE_EMBEDDED_SUPERVISOR=1` 关闭。
- 后端新增 `GET /api/jobs/{id}/events/stream` SSE 端点，订阅
  `events.jsonl` 追加并推送到前端；job 离开 `translating` 时自动关闭。
- 新增 20 个测试（17 supervisor + 3 SSE）。
- 真实链路：uvicorn + supervisor 同时跑，supervisor 真的连后端、能
  正确处理 0-job 情况、SIGTERM 干净退出。

### 2026-07-07 — P1 组件分类（BookIR ChapterKind/BlockKind）

- 新增 `src/pdf_translator/chapter_kind.py`（233 行）：定义 8 个
  chapter kinds（cover/toc/front_matter/narrative/apparatus/bibliography/
  index/appendix）和 7 个 block kinds（text/table/figure/caption/note/
  heading/list）。
- `classify_chapter()` 用 4 个信号决策：explicit kind → title hints →
  preserve+resource_only 标记 → majority page kind → 默认 narrative。
- `book_rebuild.py` 的 `build_book_reconstruction` 末尾统一调用
  `_annotate_chapter_kinds()`：给每个 chapter 写 `kind` 字段、强制
  `translate=False`（如果 kind 是非翻译类型）、归一化 block kinds。
- `book_views.py` 的 `render_translation_input_markdown()` 重写：
  跳过 apparatus/toc/bibliography/index/appendix chapters，跳过
  narrative chapter 里的 table/figure blocks，把图片替换成纯文本提示。
- 新增 43 个测试（29 chapter_kind + 14 book_views）。
- 全量回归 104 个测试通过。

### 2026-07-07 — P2 术语门槛 + 审阅豁免

- 扩展 `glossary_extraction.GENERIC_STOP_PHRASES`，过滤高频但非术语的
  通用短语。
- `glossary_extraction.py._metadata_exclusions` 扩展：之前只取前 3 章
  标题，现在取所有 chapter 标题 + 子串。
- `glossary.py.extract_glossary_candidates` 保留自适应候选上限，
  只增加 curated stop phrase 过滤和 stats 计数。
- 新增 10 个测试。
- 新增 `src/pdf_translator/review_exemptions.py`（117 行）：5 类豁免
  —— apparatus chapters、quote/code segments、foreign script
  (Arabic/Hebrew)、glossary 专名词条、纯年份 segment。
- `review.py.detect_review_items` 在 `mixed_english` 分支前调用
  `apply_review_exemptions`：被豁免的 segment 不再被判 mixed_english。
  untranslated / possibly_incomplete 不受影响（这些是真正的翻译缺陷）。
- segment 字典增加 `chapter_kind` 字段。
- 新增 15 个测试。
- 后续修订确认：没有保留固定硬阈值模块或旧 fixture。

### 2026-07-07 — P3 首页重构

- 删除旧 `Home.tsx` 后，根路由 `/` 改为重定向到 `/upload`，
  避免 Layout 渲染空 Outlet。
- `frontend/src/components/Layout.tsx` 改导航：移除"旧书库"（与
  Phase A 不符），保留书籍处理/审阅；logo 改为 BookWeaver +
  Phase A 标签。
- `frontend/src/App.test.tsx` 新增根路由回归测试。
- 前端 build / vitest 在最终验收步骤统一记录真实结果。


### 2026-07-07 — P2 修订：回滚硬阈值与硬上限

收到用户反馈后回滚了 P2 术语门槛的硬性部分：
- 撤掉 `MIN_OCCURRENCES=8 / MIN_CHAPTERS=4 / MAX_CANDIDATES=60` 硬门槛
  —— 固定数与书的体量/学科密度无关，对 100 页和 600 页的书用同一
  套数不合理。
- 撤掉 `extract_glossary_candidates` 的 `min_occurrences/min_chapters/
  min_score` 注入参数（既然生产路径不强制，也就不需要测试注入点）。
- 清理 `tests/conftest.py` 的 `legacy_glossary_thresholds` fixture 和
  老测试里的对应参数。
- 保留 `GENERIC_STOP_PHRASES` 黑名单（这是有针对性的过滤，不依赖
  数字魔数）。
- 保留 `_metadata_exclusions` 扩展到所有 chapter 标题（这是修
  bug，不是设门槛）。
- 调整术语测试反映上述变化，并加注释说明真正能改进术语提取质量的
  方向是**重写提取算法**（TF-IDF / 互信息 / 肘部法则），而不是
  手拍上限。

当前验收结果：Python/backend/core test matrix `515 passed`；前端
Vitest `23 passed`；前端 lint、production build、项目代码 compileall 均通过。

### 2026-07-08 — 审核修复：事件轮询与根路由

- 修复 `GET /api/jobs/{id}/translation-events` 读取路径：从真实
  artifact run 目录 `artifacts/*/jobs/translation-events.jsonl` 读取，
  而不是不存在的 `job_dir/jobs/translation-events.jsonl`。
- 修复 `limit` 截断时 offset 直接跳到文件末尾的问题；现在返回真实
  已消费字节数，避免客户端下次轮询跳过未返回事件。
- 修复 `/` 根路由空白页：删除旧 Home 后，根路由重定向到 `/upload`。
