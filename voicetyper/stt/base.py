from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Iterable


PartialHandler = Callable[[str], None]
FinalHandler = Callable[[str], None]
ErrorHandler = Callable[[Exception], None]


class TranscriptionBackend(ABC):
    @abstractmethod
    def stream(
        self,
        audio_source,
        on_partial: PartialHandler,
        on_final: FinalHandler,
        on_error: ErrorHandler,
    ) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...
