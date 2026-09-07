"""要約 — API を叩かない部分だけを固める。

Gemini の呼び出しそのものは外部境界なのでテストしない。ここで見るのは、
撮る時刻の畳み込みとエラーメッセージの読みやすさ。
"""

import pytest

from youtube_scribe.summary import (
    DEFAULT_MODEL,
    DEFAULT_RETRY_ATTEMPTS,
    Section,
    Summary,
    Term,
    describe_error,
    is_model_unavailable,
    model_name,
    retry_attempts,
    retry_delay,
)


def _summary(seconds: list[int | None]) -> Summary:
    return Summary(
        points=["要点"],
        sections=[Section(heading="見出し", body="本文", evidence_sec=s) for s in seconds],
        terms=[Term(word="語", original="term", meaning="意味")],
    )


def test_evidence_seconds_is_sorted_and_deduplicated() -> None:
    assert _summary([90, 10, 90, 30]).evidence_seconds() == (10, 30, 90)


def test_evidence_seconds_drops_none() -> None:
    # 根拠を特定できなかった節は、画像も撮らない。
    assert _summary([None, 5, None]).evidence_seconds() == (5,)


def test_evidence_seconds_drops_negative() -> None:
    assert _summary([-1, 0, 7]).evidence_seconds() == (0, 7)


def test_evidence_seconds_when_nothing_has_evidence() -> None:
    assert _summary([None, None]).evidence_seconds() == ()


def test_model_name_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert model_name() == DEFAULT_MODEL


def test_model_name_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    assert model_name() == "gemini-2.5-pro"


def test_model_name_ignores_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "   ")
    assert model_name() == DEFAULT_MODEL


class FakeApiError(Exception):
    """status_code を持つ、SDK の例外に似せたもの。

    本物は google.genai._gaos.lib.compat_errors にあり、公開されていない。
    だから実装もクラスではなく status_code の有無で判断している。
    """

    def __init__(self, status_code: int, message: str = "boom") -> None:
        super().__init__(message)
        self.status_code = status_code


def test_describe_error_explains_rate_limits() -> None:
    message = describe_error(FakeApiError(429))
    assert "レート制限" in message
    # 次にどこを見ればよいかまで書く。
    assert "aistudio.google.com/rate-limit" in message


def test_describe_error_explains_auth_failures() -> None:
    assert "GEMINI_API_KEY" in describe_error(FakeApiError(401))
    assert "GEMINI_API_KEY" in describe_error(FakeApiError(403))


def test_describe_error_explains_bad_requests() -> None:
    assert "スキーマ" in describe_error(FakeApiError(400))


def test_describe_error_falls_back_to_the_status_code() -> None:
    # 5xx と 4xx の既知の並び以外は、コードだけ出して人に渡す。
    assert "HTTP 404" in describe_error(FakeApiError(404))


def test_describe_error_without_a_status_code() -> None:
    # ネットワーク断など、status_code を持たない例外もそのまま扱える。
    assert "呼び出しに失敗" in describe_error(RuntimeError("接続できない"))


def test_any_surfaced_429_switches_the_model() -> None:
    """**エラーコードで判別しない。**

    公式の表は `quota_exceeded` を daily としているが、実際には RPD 20 を
    使い切っても `too_many_requests` が返ってきた（実測 18 件すべて）。
    SDK のリトライを抜けて 429 が出てきた時点で、そのモデルは今は使えない。
    """
    assert is_model_unavailable(FakeApiError(429, "too_many_requests")) is True
    assert is_model_unavailable(FakeApiError(429, "quota_exceeded")) is True
    assert is_model_unavailable(FakeApiError(429, "rate_limit_exceeded")) is True


def test_a_busy_model_also_switches_the_model() -> None:
    """**500 でも切り替える。** 混んでいるのはそのモデルだけである。

    実際に返ってきた本文はモデル名を名指ししてくる:
        gemini-3.8-flash is currently experiencing high demand
    これを 1 本の失敗として落としたため、空いているモデルを 3 つ残したまま
    「作成 0 本 / 失敗 1 本」で終わった。
    """
    assert is_model_unavailable(FakeApiError(500, REAL_500)) is True
    assert is_model_unavailable(FakeApiError(502, "bad gateway")) is True
    assert is_model_unavailable(FakeApiError(503, "unavailable")) is True
    assert is_model_unavailable(FakeApiError(504, "gateway timeout")) is True


def test_other_status_codes_do_not_switch_the_model() -> None:
    # **こちらの入力が悪いものは、モデルを変えても直らない。**
    assert is_model_unavailable(FakeApiError(400, "bad")) is False
    assert is_model_unavailable(FakeApiError(401, "unauthorized")) is False
    assert is_model_unavailable(FakeApiError(404, "not found")) is False
    assert is_model_unavailable(RuntimeError("接続できない")) is False


REAL_500 = (
    "Error code: 500 - {'error': {'message': 'gemini-3.8-flash is currently "
    "experiencing high demand, spikes in demand are usually temporary. "
    "Please try again later.', 'code': 'api_error'}}"
)


def test_describe_error_says_a_500_is_temporary() -> None:
    message = describe_error(FakeApiError(500, REAL_500))
    assert "混み合っている" in message
    # コードは残す。切り分けのときに効く。
    assert "HTTP 500" in message
    # **本文を捨てない。** どのモデルが混んでいたかは本文にしか無い。
    assert "high demand" in message


def test_describe_error_mentions_the_daily_reset() -> None:
    assert "16:00" in describe_error(FakeApiError(429, REAL_429))


def test_describe_error_names_the_rate_limit() -> None:
    message = describe_error(FakeApiError(429, "rate_limit_exceeded"))
    assert "レート制限" in message
    # 上限を見に行ける場所を必ず添える。数値はドキュメントに無い。
    assert "aistudio.google.com/rate-limit" in message


REAL_429 = (
    "Error code: 429 - {'error': {'message': 'You exceeded your current quota, "
    "please check your plan and billing details. "
    "\\n* Quota exceeded for metric: "
    "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
    "limit: 20, model: gemini-3.8-flash\\nPlease retry in 49.306074145s.', "
    "'code': 'too_many_requests'}}"
)


def test_retry_delay_reads_the_hint_from_a_real_response() -> None:
    """実際に返ってきた 429 の本文から待ち時間を読む。

    SDK の自動リトライは 1→2→4→8→16 秒の計 31 秒で諦めるので、
    「49.3 秒待て」と言われたものを失敗にしていた。
    """
    assert retry_delay(FakeApiError(429, REAL_429)) == pytest.approx(49.306074145)


def test_retry_delay_handles_short_waits() -> None:
    assert retry_delay(FakeApiError(429, "Please retry in 5.958007113s.")) == pytest.approx(
        5.958007113
    )


def test_retry_delay_is_none_without_a_hint() -> None:
    assert retry_delay(FakeApiError(429, "quota_exceeded")) is None
    assert retry_delay(RuntimeError("接続できない")) is None


def test_the_message_tells_how_long_to_wait() -> None:
    # 本文の指示をそのまま人に見せる。**ツールが自分でリトライはしない** —
    # SDK が既に 5 回・最大 60 秒で待ち直しており、重ねると 1 本で 20 リクエストになる。
    assert "49 秒後に再試行できる" in describe_error(FakeApiError(429, REAL_429))


def test_the_message_falls_back_without_a_hint() -> None:
    assert "時間をおけば続けられる" in describe_error(FakeApiError(429, "rate_limit_exceeded"))


def test_the_real_429_switches_the_model() -> None:
    # 実際に返ってきた本文。code は too_many_requests だが limit: 20 は RPD である。
    assert is_model_unavailable(FakeApiError(429, REAL_429)) is True


def test_retry_attempts_defaults_to_a_small_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK 既定の 5 回は、クォータが枯れているときには害になる。

    429 はリトライ 1 回ごとにクォータを消費する。上限 20 に対して 5 回粘ると、
    1 本の動画で 4 分の 1 を食う。
    """
    monkeypatch.delenv("GEMINI_RETRY_ATTEMPTS", raising=False)
    assert retry_attempts() == DEFAULT_RETRY_ATTEMPTS
    assert DEFAULT_RETRY_ATTEMPTS < 5


def test_retry_attempts_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_RETRY_ATTEMPTS", "4")
    assert retry_attempts() == 4


def test_retry_attempts_ignores_nonsense(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_RETRY_ATTEMPTS", "0")
    assert retry_attempts() == DEFAULT_RETRY_ATTEMPTS
    monkeypatch.setenv("GEMINI_RETRY_ATTEMPTS", "たくさん")
    assert retry_attempts() == DEFAULT_RETRY_ATTEMPTS
