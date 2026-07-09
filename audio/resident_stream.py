"""단일 상주 sd.OutputStream을 통해 PCM을 재생하는 공유 오디오 출력 관리자.

호출마다 ALSA 디바이스를 열고 닫으면 ES8388 코덱/스피커 앰프가 반복적으로
켜졌다 꺼지며 전류 스파이크를 유발한다 (보조배터리 OCP 트립 위험). 프로세스
구동 중 이 스트림 하나만 열어둔 채 유지하고, 재생할 PCM은 콜백이 소비하는
내부 버퍼에 채워 넣는 방식으로 디바이스 open/close 자체를 없앤다.
"""
import logging
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class ResidentAudioStream:
    """프로세스 구동 중 유지되는 단일 sd.OutputStream 래퍼.

    play()로 채워 넣은 스테레오 PCM을 PortAudio 콜백 스레드가 순차 소비한다.
    재생할 데이터가 없으면 무음을 흘려보내 스트림을 계속 연 상태로 유지한다.
    """

    def __init__(self, samplerate: int, device: Optional[int] = None) -> None:
        self._samplerate = samplerate
        self._device = device
        self._stream = None
        self._lock = threading.Lock()
        self._buffer = np.zeros((0, 2), dtype=np.float32)
        self._pos = 0

    def start(self) -> None:
        """상주 출력 스트림을 연다. 실패 시 경고만 남기고 재생은 이후 무시된다."""
        try:
            import sounddevice as sd

            self._stream = sd.OutputStream(
                samplerate=self._samplerate,
                channels=2,
                dtype="float32",
                device=self._device,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            logger.warning("상주 오디오 스트림 열기 실패, 재생 비활성화: %s", exc)
            self._stream = None

    def _callback(self, outdata, frames: int, time_info, status) -> None:
        """PortAudio가 오디오 스레드에서 주기적으로 호출하는 콜백."""
        if status:
            logger.warning("오디오 콜백 상태 경고: %s", status)
        with self._lock:
            remaining = len(self._buffer) - self._pos
            n = min(frames, max(remaining, 0))
            if n > 0:
                outdata[:n] = self._buffer[self._pos : self._pos + n]
                self._pos += n
            if n < frames:
                outdata[n:] = 0.0

    def play(self, stereo: np.ndarray, interrupt: bool = False) -> None:
        """스테레오 PCM을 재생 버퍼에 채운다 (논블로킹, 즉시 반환).

        Args:
            stereo: (n_samples, 2) 형태의 float32 스테레오 PCM.
            interrupt: True면 현재 재생/대기 중인 오디오를 즉시 버리고 교체한다.
                False면 남은 재생 분량 뒤에 이어 붙인다.
        """
        if self._stream is None:
            return
        with self._lock:
            if interrupt:
                self._buffer = stereo
            else:
                self._buffer = np.concatenate([self._buffer[self._pos :], stereo])
            self._pos = 0

    def is_playing(self) -> bool:
        """재생 대기 중인 데이터가 남아있으면 True를 반환한다."""
        with self._lock:
            return self._pos < len(self._buffer)

    def clear(self) -> None:
        """재생 대기 중인 버퍼를 즉시 비운다 (선점 시 사용)."""
        with self._lock:
            self._buffer = np.zeros((0, 2), dtype=np.float32)
            self._pos = 0

    def stop(self) -> None:
        """스트림을 닫는다."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
