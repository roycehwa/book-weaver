# Knowledge Profiles for Book Decomposition

本文件定义“书籍内容拆解 → 知识网络”的高层方法。核心结论：不同书类不能共用同一套抽取 schema。系统必须先判断书籍 profile，再选择合适的知识化流程。

## 原则

- `book.json` / BookIR 仍是事实源；Markdown 是视图，不是结构源。
- 先章节化，再语义单元化，再抽取知识；不要直接从整本书生成图谱。
- 每个知识点必须保留 provenance：`book_id`、`chapter_id`、页码、段落序号、原文/译文片段 hash。
- 自动抽取只生成候选知识；高价值输出需要支持人工修正和回写。
- 不追求“抽尽所有内容”；优先高置信、可复用、可追溯。
- 每个 profile 都要声明“不适合抽取什么”，防止模型过度解释。

## Profile Matrix

| Profile | 典型书类 | 主要知识形态 | 推荐输出 | 不推荐输出 |
|---|---|---|---|---|
| `argumentative` | 学术专著、哲学、法学、政治学、人文社科 | 概念、论点、证据、反驳、理论谱系 | argument map、概念 wiki、claim/evidence 索引 | 纯实体三元组图谱 |
| `textbook` | 教材、培训书、技术入门 | 学习目标、术语、定义、先修关系、例题 | 学习路线、术语表、概念图、复习卡 | 作者论证网络 |
| `historical` | 历史、传记、新闻纪实 | 人物、事件、时间、地点、因果链 | timeline、人物关系、事件图谱 | 抽象概念网络为主的图谱 |
| `practical` | 管理、商业、方法论、操作手册 | 框架、原则、步骤、案例、行动建议 | playbook、checklist、流程图、案例库 | 大规模开放关系图谱 |
| `narrative` | 小说、文学、叙事非虚构 | 人物、场景、事件、冲突、主题/母题 | 情节流、人物关系变化、主题索引 | claim/evidence 学术论证模型 |
| `technical_lite` | 工程、数学、公式/图表密集书 | 定义、定理、公式、图表、步骤 | 章节目录、术语/定理/公式/图表索引 | 自动语义还原公式或证明图谱 |

## 1. Argumentative Profile

适用对象：学术专著、理论书、哲学、政治学、社会科学、人文学术书。

核心任务不是抽实体，而是抽“作者如何论证”。

### 抽取对象

- `concept`: 作者使用或重新定义的核心概念。
- `claim`: 作者明确提出的判断、解释、立场。
- `evidence`: 用于支撑 claim 的例子、引用、数据、史料。
- `counterclaim`: 作者反驳或修正的观点。
- `theory_relation`: 与已有理论的支持、反对、继承、修正关系。

### 关系 schema

- `defines`
- `supports`
- `challenges`
- `revises`
- `contrasts_with`
- `uses_as_evidence`
- `derived_from`

### 输出

- 每章 argument map。
- 全书核心概念 wiki。
- claim → evidence 表。
- 理论谱系图。

### 风险

- 模型容易把“背景介绍”误判为作者主张。
- 隐含论证需要谨慎，不应无证据补全。
- 不能只做实体关系图，否则会丢掉书的价值。

## 2. Textbook Profile

适用对象：教材、课程书、培训书、技术入门书。

核心任务是学习路径，不是论证或事件。

### 抽取对象

- `learning_objective`: 本章希望读者掌握的能力。
- `term`: 术语、定义、别名。
- `concept`: 教学概念。
- `prerequisite`: 学习某概念前应理解的概念。
- `example`: 示例、例题、练习。
- `procedure`: 操作步骤或解题方法。

### 关系 schema

- `defines`
- `requires`
- `teaches`
- `applies`
- `example_of`
- `part_of`

### 输出

- 学习目标表。
- 术语表。
- prerequisite concept map。
- 章节复习卡。
- 练习/例题索引。

### 风险

- Bloom 分类可用于标注学习目标层级，但不能机械套用动词表。
- 先修关系需要结合章节顺序、出现频率、定义位置，不能只靠一句话抽取。

## 3. Historical Profile

适用对象：历史、传记、战争、外交、新闻纪实、制度演化类书。

核心任务是事件结构。

### 抽取对象

- `actor`: 人物、组织、国家、机构。
- `event`: 发生了什么。
- `time`: 明确时间或相对时间。
- `place`: 地点。
- `causal_link`: 作者明确提出的因果关系。
- `temporal_link`: before / after / during / overlaps。

### 关系 schema

- `participates_in`
- `happened_at`
- `happened_in`
- `caused`
- `enabled`
- `responded_to`
- `before`
- `after`

### 输出

- timeline。
- 人物/组织关系图。
- 事件因果链。
- 地点索引。

### 风险

- 时间关系常常模糊，不能强行排序。
- 历史解释与事实事件要分开：event 是事实叙述，interpretation 是作者解释。

## 4. Practical Profile

适用对象：商业、管理、自助、方法论、组织流程、操作手册。

核心任务是把书变成可执行 playbook。

### 抽取对象

- `framework`: 作者提出的框架或模型。
- `principle`: 原则。
- `step`: 步骤。
- `rule`: 规则、判断标准。
- `case`: 案例。
- `action`: 可执行建议。
- `anti_pattern`: 作者提醒避免的做法。

### 关系 schema

- `part_of_framework`
- `next_step`
- `applies_when`
- `avoid_when`
- `illustrated_by`
- `depends_on`

### 输出

- checklist。
- playbook。
- 案例库。
- 流程图。
- 决策树。

### 风险

- 管理书常有大量故事和修辞，不应全部知识化。
- 输出应面向行动，而不是生成庞大概念图。

## 5. Narrative Profile

适用对象：小说、文学、叙事非虚构。

核心任务是叙事结构。

### 抽取对象

- `character`
- `scene`
- `event`
- `relationship_state`
- `conflict`
- `theme`
- `motif`

### 关系 schema

- `appears_in`
- `interacts_with`
- `changes_relationship_with`
- `causes_event`
- `reveals_theme`
- `foreshadows`

### 输出

- 情节流。
- 人物关系变化图。
- 场景索引。
- 主题/母题索引。

### 风险

- 文学意义高度依赖解释，自动抽取只能做候选。
- 不应使用 claim/evidence 模型。

## 6. Technical Lite Profile

适用对象：数学、工程、公式密集、图表密集、证明密集书。

当前阶段只做弱支持。

### 抽取对象

- `definition`
- `theorem`
- `formula_placeholder`
- `table`
- `figure`
- `procedure`
- `symbol`

### 输出

- 章节目录。
- 定义/定理/公式/图表索引。
- 术语表。
- 人工修正入口。

### 明确不承诺

- 不自动还原复杂公式语义。
- 不自动生成证明依赖图。
- 不把损坏公式碎片当作可靠知识。

## Suitability Report

任何知识化流程前，先生成 `knowledge/suitability-report.json`：

```json
{
  "profile": "argumentative",
  "confidence": 0.78,
  "secondary_profiles": ["historical"],
  "network_suitability": "high",
  "recommended_outputs": ["argument_map", "concept_wiki", "claim_evidence_index"],
  "extractable_objects": ["concept", "claim", "evidence", "theory_relation"],
  "do_not_extract": ["timeline_as_primary_output", "formula_graph"],
  "risks": [
    "background literature may be confused with author claims",
    "implicit arguments require conservative extraction"
  ]
}
```

## Implementation Phases

### Phase 0: deterministic structure

No model call.

- Build `knowledge/chapters.json` from `book.json`.
- Build `knowledge/semantic-units.json` from chapters.
- Preserve page ranges, paragraph indexes, asset refs, source hashes.
- Emit profile-neutral Markdown wiki stubs.

### Phase 1: profile classification

Low-cost model call per book.

- Input: metadata, TOC, chapter titles, sampled chapter openings.
- Output: suitability report.
- Human may override profile.

### Phase 2: profile-specific extraction

Model calls per chapter or semantic unit.

- Use strict schema per profile.
- Require evidence for every extracted item.
- Store raw extraction candidates separately from accepted knowledge.

### Phase 3: normalization and merge

- Entity/concept canonicalization.
- Alias / translation / original-language name mapping.
- Duplicate claim and concept merge.
- Confidence scoring.

### Phase 4: outputs

- `knowledge/wiki/`
- `knowledge/indexes/`
- `knowledge/graphs/*.json`
- `knowledge/mindmap.mmd`
- Optional later: Notion / Obsidian / Neo4j export.

## References

- Argument mining: claim, premise, support, attack structures.
- Educational knowledge graphs: concept prerequisites and learning paths.
- Bloom taxonomy: learning objective classification for textbook/training profile.
- Event-centric temporal knowledge graphs: event/time/place/actor modeling.
- Narrative extraction and character networks: fiction and narrative profile.
- Procedural knowledge extraction: practical / how-to / playbook profile.
