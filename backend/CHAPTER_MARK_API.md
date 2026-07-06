# BookMate Phase 2 - 章节标记 API 文档

## 概述

Phase 2 P0 实现了用户章节标记的存储和处理 API，支持：
- 创建用户章节标记
- 删除用户章节标记
- 标记后自动重新分段

## API 端点

### 1. 创建章节标记

**POST** `/api/books/{book_id}/chapters/mark`

在用户指定的位置创建章节标记，并自动触发重新分段。

#### 请求参数

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| page_number | int | ✅ | 页码（1-based） |
| y_position | float | ✅ | 页面垂直位置（0.0-1.0 归一化） |
| chapter_name | string | ❌ | 可选的章节名称（AI提取或用户输入） |

#### 请求示例

```json
{
  "page_number": 5,
  "y_position": 0.3,
  "chapter_name": "Introduction"
}
```

#### 响应示例

```json
{
  "book_id": "uuid-string",
  "mark": {
    "mark_id": "mark-uuid",
    "page_number": 5,
    "y_position": 0.3,
    "chapter_name": "Introduction",
    "created_at": "2024-03-26T11:30:00"
  },
  "chapters": [
    {
      "index": 0,
      "title": "Original Chapter 1",
      "content": "...",
      "page_number": 1,
      "end_page": 5
    },
    {
      "index": 1,
      "title": "Introduction",
      "content": "...",
      "page_number": 5,
      "end_page": 10,
      "is_user_mark": true,
      "mark_id": "mark-uuid"
    }
  ],
  "message": "Chapter mark created successfully, chapters recalculated"
}
```

### 2. 删除章节标记

**DELETE** `/api/books/{book_id}/chapters/{chapter_id}`

删除用户创建的章节标记，并自动触发重新分段。

#### 路径参数

| 参数 | 描述 |
|------|------|
| book_id | 书籍唯一标识 |
| chapter_id | 章节ID（对应用户标记的 mark_id） |

#### 响应示例

```json
{
  "book_id": "uuid-string",
  "deleted_mark_id": "mark-uuid",
  "chapters": [...],
  "message": "Chapter mark deleted successfully, chapters recalculated"
}
```

### 3. 获取书籍标记列表

**GET** `/api/books/{book_id}/marks`

获取指定书籍的所有用户标记。

#### 响应示例

```json
{
  "book_id": "uuid-string",
  "total_marks": 2,
  "marks": [
    {
      "mark_id": "mark-uuid-1",
      "page_number": 5,
      "y_position": 0.3,
      "chapter_name": "Introduction",
      "created_at": "2024-03-26T11:30:00"
    },
    {
      "mark_id": "mark-uuid-2",
      "page_number": 20,
      "y_position": 0.5,
      "chapter_name": "Chapter 2",
      "created_at": "2024-03-26T11:35:00"
    }
  ]
}
```

## 数据结构

### Book 对象更新

在原有的 book 对象中增加了 `user_marks` 数组：

```json
{
  "metadata": {...},
  "chapters": [...],
  "user_marks": [
    {
      "mark_id": "uuid-string",
      "page_number": 5,
      "y_position": 0.3,
      "chapter_name": "Introduction",
      "created_at": "2024-03-26T11:30:00"
    }
  ]
}
```

### Chapter 对象更新

Chapter 对象增加了两个字段：

| 字段 | 类型 | 描述 |
|------|------|------|
| is_user_mark | boolean | 是否为用户标记创建的章节 |
| mark_id | string/null | 关联的用户标记ID |

## 重新分段逻辑

当用户创建或删除标记时，系统会自动重新分段：

1. **硬边界规则**：用户标记位置作为硬边界，优先于 TOC 提取的章节边界
2. **合并策略**：同一页面且 y 位置差 < 0.05 的边界会合并，用户标记优先
3. **内容提取**：使用 PyMuPDF 根据边界精确提取文本内容
4. **章节索引**：重新计算的章节会重新编号

## 文件位置

- **API 端点**: `backend/main.py`
- **服务实现**: `backend/app/services/chapter_mark_service.py`
- **存储模型**: `backend/storage.py`

## 测试

运行测试脚本：

```bash
cd /root/.openclaw/workspace/bookmate/backend
python test_chapter_marks.py
```

## 注意事项

1. 删除操作只能删除 `is_user_mark=true` 的章节
2. 页码必须在有效范围内（1 <= page_number <= total_pages）
3. y_position 是归一化值（0.0 = 页面顶部，1.0 = 页面底部）
4. 重新分段会修改书籍的章节列表，原始 TOC 章节可能被分割
