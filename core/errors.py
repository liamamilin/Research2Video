"""Shared exception types for the research engine."""


class CancelledError(Exception):
    """Raised when a job is cancelled via the cancel token."""


class LLMError(Exception):
    """Base error for LLM API failures."""


class LLMConfigError(LLMError):
    """Raised when the LLM configuration is incomplete or invalid."""


class LLMUnsupportedError(LLMError):
    """Raised when the model/endpoint rejects tool calling.

    Signals the research agent to fall back to pipeline mode.
    """


class SearchUnavailableError(Exception):
    """Raised when no search provider could answer (quota, key, rate limit, network).

    A run must stop on this: carrying on would write the report from the model's
    own memory and mark it a success. ``reasons`` maps provider -> why it failed.
    """

    def __init__(self, reasons: dict[str, str]):
        self.reasons = dict(reasons)
        detail = "；".join(f"{name}: {why}" for name, why in self.reasons.items())
        super().__init__(
            f"联网搜索不可用，已中止，没有生成报告。{detail}。"
            "请到「设置 → 搜索」检查服务商额度和 Key，或换一个搜索服务商后重新运行。"
        )


class SourcesUnavailableError(Exception):
    """Raised when a Job that reads only its declared sources fetched nothing from any of them.

    Such a Job has no web search to fall back on: an empty source block would let the model write from memory.
    ``failures`` maps source spec -> why it gave nothing.
    """

    def __init__(self, failures: dict[str, str]):
        self.failures = dict(failures)
        detail = "；".join(f"{spec}: {why}" for spec, why in self.failures.items())
        super().__init__(f"所有订阅源都没有抓到内容，已中止，没有生成报告。{detail}。检查网络，或网站是否改版。")
