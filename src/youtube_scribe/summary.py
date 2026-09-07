"""要約 — 書き起こしから、記事の中身と**画面キャプチャを撮る時刻**を決める。

このモジュールは書き起こしがどう作られたかを知らない。受け取るのは
「秒 + テキスト」の並びだけである。

**Gemini API を import してよいのはここだけ。** 経路は Interactions API で、
`trend-sight` が採っているものと同じ（公式が "recommended for all new projects"
と述べている側）。無料枠を使う前提なので、送った字幕と生成された要約は Google の
製品改善に使われ、人間がレビューし得る。**機密を含む動画には使わないこと。**
"""

from __future__ import annotations

import os
import re

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from youtube_scribe.transcript import Transcript
from youtube_scribe.video import Video

MODEL_ENV = "GEMINI_MODEL"

# 無料枠で使える最上位の Flash。公式の紹介文は "our most intelligent Flash model"。
# 現行最上位の gemini-3.1-pro-preview は無料枠では使えない（"Not available"）。
# 提供終了に備えて GEMINI_MODEL で上書きできるようにしてある。
DEFAULT_MODEL = "gemini-3.8-flash"

# **クォータはモデルごとに完全に独立している**（AI Studio で実測）。無料枠は
# Flash 系が RPD 20 / RPM 5、Flash Lite 系が RPD 500 / RPM 15。1 本 = 1 リクエストなので、
# 既定のモデルを使い切っても、次のモデルへ移れば同じ日に続けられる。
#
# 並びは品質の高い順。最後の Flash Lite は上限が 25 倍あるので、実質の逃げ道になる。
DEFAULT_FALLBACK_MODELS = ("gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite")
FALLBACK_ENV = "GEMINI_FALLBACK_MODELS"

# `cli` に Gemini SDK を import させないための別名。
Client = genai.Client

# trend-sight と同じ 180 秒。長い書き起こしでも足りる。
_TIMEOUT_SEC = 180.0

# SDK の既定は 5 回（`_RETRY_ATTEMPTS = 5`）だが、**429 はリトライ 1 回ごとに
# クォータを消費する。** 無料枠の上限は 20（実測）なので、5 回粘ると 1 本で 4 分の 1 を
# 食う。枯れているときに粘っても回復しないため、少ない回数で諦めて次の実行に回す。
RETRY_ATTEMPTS_ENV = "GEMINI_RETRY_ATTEMPTS"
DEFAULT_RETRY_ATTEMPTS = 2

# 429 の本文にある「何秒後に再試行せよ」。実際にこう返る:
#   Please retry in 5.958007113s.
# **メッセージに出すためだけに使う。自分でリトライしない。**
_RETRY_HINT = re.compile(r"retry in ([\d.]+)\s*s", re.IGNORECASE)

_SYSTEM = """\
あなたは YouTube 動画の内容を、日本語の学習メモに変換する。読み手は動画を一度見た本人で、
あとから読み返して思い出すために使う。

入力は書き起こしで、各行は `[秒] 発話` の形をしている。秒は動画の先頭からの経過秒である。

守ること:
- **書き起こしに無いことを書かない。** 一般論で埋めない。
- 記事は日本語で書く。技術用語・固有名詞は初出時に原語を併記する。
- `evidence_sec` には、その節の根拠になった発話の秒をそのまま入れる。書き起こしの
  行頭にある数値から取る。特定できない節は null にする。
- 図・スライド・コード画面など、**見せたほうが早いものを扱っている節にこそ**
  `evidence_sec` を入れる。口頭の説明だけで完結する節には無理に入れなくてよい。
- `points` は動画全体が伝えたいことを 3〜5 個、それぞれ 1 文で。
- `sections` は要点の根拠を掘り下げる。動画の流れに沿って並べる。
- `terms` は初出の専門語だけ。動画を見た人が知らなそうなものに絞る。
"""


class Section(BaseModel):
    """記事の「詳細」を構成する 1 節。"""

    heading: str = Field(description="節の見出し")
    body: str = Field(description="節の本文。2〜5 文程度")
    evidence_sec: int | None = Field(
        description="この節の根拠になった発話の秒。特定できなければ null"
    )


class Term(BaseModel):
    word: str = Field(description="日本語での呼び方")
    original: str = Field(description="原語表記。日本語しか無ければ空文字")
    meaning: str = Field(description="1〜2 文の説明")


class Summary(BaseModel):
    points: list[str] = Field(description="動画が伝えたいことを 3〜5 個、各 1 文")
    sections: list[Section]
    terms: list[Term]

    def evidence_seconds(self) -> tuple[int, ...]:
        """画面キャプチャを撮る時刻。**重複は畳み、昇順にする。**"""
        found = {s.evidence_sec for s in self.sections if s.evidence_sec is not None}
        return tuple(sorted(second for second in found if second >= 0))


class SummaryFailedError(RuntimeError):
    """要約が得られなかった。動画 1 本を落とす理由になる。"""


class ModelUnavailableError(SummaryFailedError):
    """このモデルは今は使えない。**別のモデルに切り替える合図。**

    理由は 2 通りある。**どちらも同じ扱いでよい。**

    - **429** — こちらが上限に当たった。クォータはモデルごとに独立しているので、
      次のモデルは 0/20 のままである
    - **5xx** — 向こうが混んでいる。実際に返ってくる本文は
      `gemini-3.8-flash is currently experiencing high demand` で、
      **モデル名を名指しして混雑を伝えてくる**（実測）。別のモデルなら空いている

    **コードで細かく分岐しない。** 公式のエラーコード表は `quota_exceeded` を
    daily、`too_many_requests` を短時間の集中としているが、実際には
    **RPD 20 を使い切っても `too_many_requests` が返る**（実測）。
    表と挙動が食い違っているので、当てにできない。

    判断はもっと単純でよい。**SDK が既に何度か試したうえで断られたのなら、
    そのモデルは今は使えない。** 次のモデルへ移ればよく、全部試し終えたときだけ
    全体を止める。
    """


def model_name() -> str:
    """使うモデル。`GEMINI_MODEL` で上書きできる（`trend-sight` と同じ流儀）。"""
    return os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL


def fallback_models() -> tuple[str, ...]:
    """既定のモデルが枯れたときに順に試すモデル。`GEMINI_FALLBACK_MODELS` で上書き可。

    空文字を渡せばフォールバックしない。
    """
    raw = os.environ.get(FALLBACK_ENV)
    if raw is None:
        return DEFAULT_FALLBACK_MODELS
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def retry_attempts() -> int:
    """SDK に何回まで試させるか。`GEMINI_RETRY_ATTEMPTS` で上書きできる。"""
    raw = os.environ.get(RETRY_ATTEMPTS_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_RETRY_ATTEMPTS


def new_client() -> Client:
    """API クライアントを作る。

    引数を渡さないと SDK が環境から鍵を解決する。`GEMINI_API_KEY` と
    `GOOGLE_API_KEY` の両方が設定されていると **`GOOGLE_API_KEY` が優先され**、
    SDK が警告を出す。**鍵をコードに書かない。**

    リトライ回数を既定より絞る。**429 は 1 回のリトライごとにクォータを消費する**ので、
    枯れているときに粘ると回復を遠ざけるだけである。
    """
    return genai.Client(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=retry_attempts()),
        ),
    )


def _user_message(video: Video, transcript: Transcript) -> str:
    chapters = "\n".join(f"[{c.start_sec}] {c.title}" for c in video.chapters)
    blocks = [
        f"# 動画\nタイトル: {video.title}\n公開日: {video.published_at}",
        f"# 説明欄\n{video.description.strip()}" if video.description.strip() else "",
        f"# チャプター\n{chapters}" if chapters else "",
        f"# 書き起こし（{transcript.source.label} / {transcript.language}）\n"
        f"{transcript.as_prompt_text()}",
    ]
    return "\n\n".join(block for block in blocks if block)


def describe_error(error: Exception) -> str:
    """API の失敗を、人が読んで次の一手が分かる 1 行にする。

    **例外クラスを import して分岐しない。** Interactions API が投げる例外は
    `google.genai._gaos.lib.compat_errors` にあり、公開されている
    `google.genai.errors` とは別の階層で、`errors.APIError` を継承していない
    （実測で確認済み）。私有モジュールに依存するより、`status_code` を持っているかで
    判断するほうが壊れにくい。
    """
    code = getattr(error, "status_code", None)
    if code == 429:
        # **応答の中身を捨てないこと。** 分あたりなのか日あたりなのかは
        # `message` に載る `code` でしか判別できない。
        # 上限の数値はドキュメントに無く、AI Studio でしか見られないので必ず案内する。
        where = "上限は https://aistudio.google.com/rate-limit で確認できる"
        wait = retry_delay(error)
        when = f"{wait:.0f} 秒後に再試行できる" if wait else "時間をおけば続けられる"
        return (
            f"Gemini のレート制限に当たった（{when}。1 日ぶんの上限なら"
            f"太平洋時間の深夜＝日本時間 16:00 前後にリセットされる）。{where}: {error}"
        )
    if code == 400:
        return f"Gemini がリクエストを拒否した（スキーマか入力長を疑う）: {error}"
    if code in (401, 403):
        return f"Gemini の認証に失敗した（GEMINI_API_KEY を確認）: {error}"
    if isinstance(code, int) and 500 <= code < 600:
        # **こちらに直せるところが無い。** 本文はモデル名を名指しして
        # "currently experiencing high demand" と言ってくる（実測）。
        return f"Gemini が混み合っている（HTTP {code}。時間をおけば戻る）: {error}"
    if isinstance(code, int):
        return f"Gemini が HTTP {code} を返した: {error}"
    return f"Gemini の呼び出しに失敗した: {error}"


def is_model_unavailable(error: Exception) -> bool:
    """SDK のリトライを抜けてきた 429 か 5xx か。**それだけで切り替える理由になる。**

    エラーコードは見ない。実測では RPD を使い切っても `too_many_requests` が返り、
    公式の表（`quota_exceeded` = daily）と一致しなかった。

    5xx を含めるのは、**500 で 1 本を落として空いているモデルを 3 つ残した**
    実績があるため。`gemini-3.8-flash is currently experiencing high demand` は
    モデル固有の混雑であって、こちらに直せるところが無い。

    `model_index` は前へしか進まないので、これで増えるリクエストは
    1 回の実行あたり「切り替えの回数 x SDK の試行回数」で止まる。
    """
    code = getattr(error, "status_code", None)
    if not isinstance(code, int):
        return False
    return code == 429 or 500 <= code < 600


def retry_delay(error: Exception) -> float | None:
    """429 の本文が指定してくる待ち時間。**表示のためだけに読む。**

    **ここで自分でリトライしてはいけない。** SDK が既に
    `_RETRY_ATTEMPTS = 5` / `_RETRY_MAX_DELAY = 60.0` で待ち直しており、
    54 秒程度の指示なら SDK の中で吸収される。

    外側にもう一段ループを足したところ、1 本の動画で最大 5 × 4 = 20 リクエストを
    投げることになり、**無料枠の `limit: 20` をその 1 本で使い切った。**
    多重にリトライを重ねないこと。
    """
    matched = _RETRY_HINT.search(str(error))
    if matched is None:
        return None
    delay = float(matched.group(1))
    return delay if delay > 0 else None


def write(video: Video, transcript: Transcript, client: Client, model: str) -> Summary:
    """1 本の動画を要約する。

    `store=False` を明示している。既定は `store=True` で、**無料枠では
    インタラクションが 1 日保持される**（有料は 55 日）。保持を使う機能
    （`previous_interaction_id` / `background`）は使っていないので、切って困らない。
    """
    # **リトライは SDK に任せる。** ここでループを重ねると、1 回の失敗が
    # 5 リクエストではなく 20 リクエストになり、無料枠を 1 本で食い潰す。
    try:
        interaction = client.interactions.create(
            model=model,
            input=_user_message(video, transcript),
            system_instruction=_SYSTEM,
            store=False,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": Summary.model_json_schema(),
            },
            timeout=_TIMEOUT_SEC,
        )
    except Exception as error:
        if is_model_unavailable(error):
            raise ModelUnavailableError(describe_error(error)) from error
        raise SummaryFailedError(describe_error(error)) from error

    # `create` の戻り値は `Interaction | Stream[...]` の union で、`stream=True` を
    # 渡していないので実体は前者になる。ただし **`Interaction` 型は私有パス
    # (`google.genai._gaos.types`) にしか無い**ので、クラスで判定せず属性で絞る。
    text = getattr(interaction, "output_text", None)
    if not isinstance(text, str) or not text.strip():
        raise SummaryFailedError("Gemini が空の応答を返した")
    return Summary.model_validate_json(text)
