from pdf_translator.repair import (
    RepairLane,
    classify_export_message,
    missing_required_images,
    record_system_repair,
)


def test_missing_images_are_skipped_by_the_program():
    case = missing_required_images(33)

    assert case.lane == RepairLane.DETERMINISTIC
    assert case.code == "missing_required_images"
    assert "33" in case.user_summary
    assert "不需要你连接图片" in case.user_summary
    assert "修正台" not in case.user_summary
    assert ".xhtml" not in case.user_summary
    assert "/" not in case.user_summary
    assert case.choices == ()


def test_navigation_drift_stays_a_recorded_note():
    case = classify_export_message("链接目标发生变化")

    assert case.lane == RepairLane.DETERMINISTIC
    assert case.choices == ()
    assert "不需要你修改" in case.user_summary


def test_segment_gap_stays_a_translation_task():
    case = classify_export_message("导出被阻止：4 个片段尚无输出，请补译或明确保留原文。")

    assert case.lane == RepairLane.BOUNDED_MODEL
    assert "审阅" in case.user_summary
    assert "结构" not in case.user_summary


def test_unknown_structure_is_not_a_user_task():
    case = classify_export_message("导出被阻止：某个内部结构断言失败 /tmp/book.xhtml#a1")

    assert case.lane == RepairLane.DETERMINISTIC
    assert case.choices == ()
    assert "不需要你改结构" in case.user_summary
    assert ".xhtml" not in case.user_summary
    assert "/tmp" not in case.user_summary


def test_model_repairs_an_image_reference_the_rules_cannot_predict():
    calls: list[str] = []

    def complete(system: str, user: str) -> str:
        calls.append(user)
        return "![图](book-images/photo.jpg)"

    from pdf_translator.repair import repair_image_references

    fixed = repair_image_references(
        "前文。\n\n![图](Title's Work (Press)/book-images/photo.jp)\n\n后文。",
        broken_srcs=["Title's Work (Press)/book-images/photo.jp"],
        available_names=["photo.jpg"],
        complete=complete,
    )

    assert calls
    assert "photo.jp" in calls[0]
    assert "photo.jpg" in calls[0]
    assert "前文。" in fixed
    assert "后文。" in fixed
    assert "book-images/photo.jpg" in fixed
    assert "Title's Work (Press)/book-images/photo.jp" not in fixed


def test_model_gets_a_second_try_when_the_image_is_still_wrong():
    answers = iter(["![图](still-wrong.jp)", "![图](book-images/photo.jpg)"])

    from pdf_translator.repair import repair_image_references

    fixed = repair_image_references(
        "![图](book-images/photo.jp)",
        broken_srcs=["book-images/photo.jp"],
        available_names=["photo.jpg"],
        complete=lambda _system, _user: next(answers),
    )

    assert fixed == "![图](book-images/photo.jpg)"


def test_polish_fixes_structure_lines_and_leaves_prose():
    from pdf_translator.repair import polish_structure_markdown

    source = "\n".join([
        "# Chapter",
        "",
        "Body.",
        "",
        "- item",
        "",
        "| a |",
        "",
        "![图](images/a.jpg)",
        "",
        "[note](chapter.xhtml#n1)",
    ])
    translated = "\n".join([
        "# 章",
        "",
        "正文。",
        "",
        "- 项",
        "",
        "| 甲 |",
        "",
        "![图](images/a.jp)",
        "",
        "[注](chapter.xhtml#n1)",
    ])
    seen: list[str] = []

    def complete(_system: str, user: str) -> str:
        seen.append(user)
        return "\n".join([
            "# 第一章",
            "- 条目",
            "| 表 |",
            "![图](images/a.jpg)",
            "[注](chapter.xhtml#n1)",
        ])

    fixed = polish_structure_markdown(
        translated,
        source_markdown=source,
        complete=complete,
        available_image_names=["a.jpg"],
    )

    assert seen
    prompt = seen[0]
    assert "# Chapter" in prompt
    assert "- item" in prompt
    assert "| a |" in prompt
    assert "images/a.jpg" in prompt
    assert "chapter.xhtml#n1" in prompt
    assert "Body." not in prompt
    assert "正文。" not in prompt
    assert "正文。" in fixed
    assert "# 第一章" in fixed
    assert "- 条目" in fixed
    assert "| 表 |" in fixed
    assert "![图](images/a.jpg)" in fixed
    assert "[注](chapter.xhtml#n1)" in fixed


def test_polish_joins_a_wrapped_sentence_and_keeps_the_next_paragraph():
    from pdf_translator.repair import polish_wrapped_lines

    original = "这是一句还没有说完\n的话。\n\n下一段。\n\n# 标题\n\n- 列表"
    seen: list[str] = []

    def complete(_system: str, user: str) -> str:
        seen.append(user)
        return "这是一句还没有说完的话。"

    fixed = polish_wrapped_lines(original, complete=complete)

    assert seen == ["这是一句还没有说完\n的话。"]
    assert fixed == "这是一句还没有说完的话。\n\n下一段。\n\n# 标题\n\n- 列表"


def test_polish_rejects_a_join_that_changes_the_words():
    from pdf_translator.repair import polish_wrapped_lines

    original = "这是一句还没有说完\n的话。"

    def complete(_system: str, _user: str) -> str:
        return "这是另一句已经改写的话。"

    assert polish_wrapped_lines(original, complete=complete) == original


def test_structure_repair_keeps_prose_when_the_model_fails():
    from pdf_translator.repair import repair_structure_markdown

    original = "# 第一章\n\n正文很长，不应被整章重写。"

    def _fail(_system: str, _user: str) -> str:
        raise ValueError("truncated")

    assert repair_structure_markdown(original, problem="标题不一致", complete=_fail) == original


def test_system_repair_log_records_the_count_without_paths(tmp_path):
    record_system_repair(tmp_path, missing_required_images(2))

    text = (tmp_path / "repair-log.json").read_text(encoding="utf-8")
    assert "missing_required_images" in text
    assert "2" in text
    assert "/" not in text
    assert ".jpg" not in text
