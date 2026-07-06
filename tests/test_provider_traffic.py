from pdf_translator.provider_traffic import ProviderTrafficController


def test_overload_reduces_concurrency_and_success_recovers_gradually() -> None:
    controller = ProviderTrafficController(max_concurrency=6, rpm=500, tpm=20_000_000)

    controller.record_overload(retry_after=0)
    assert controller.current_concurrency == 3

    controller.record_success()
    assert controller.current_concurrency == 3
    for _ in range(7):
        controller.record_success()
    assert controller.current_concurrency == 4


def test_request_interval_uses_rpm_limit() -> None:
    controller = ProviderTrafficController(max_concurrency=3, rpm=20, tpm=1_000_000)

    assert controller.minimum_request_interval == 3.0


def test_request_interval_also_respects_estimated_tpm() -> None:
    controller = ProviderTrafficController(max_concurrency=3, rpm=500, tpm=60_000)

    assert controller.request_interval(estimated_tokens=10_000) == 10.0
