from pdf_translator.table_translate import run_translate_tables


def test_run_translate_tables_public_entrypoint_is_callable() -> None:
    assert callable(run_translate_tables)
