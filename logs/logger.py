"""CSV 기반 RasEyes 운영 로거."""
import csv
import datetime
import logging
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)


class CsvLogger:
    """1초 1회 운영 데이터를 CSV 파일에 기록하는 로거.

    세션(프로세스 실행)마다 새 파일에 기록한다. 단일 경로에 mode="w"로 쓰던
    이전 방식은 재시작 때마다 직전 세션을 덮어써, 2026-07-28 야외 테스트 로그가
    통째로 소실됐다.

    Args:
        path: CSV 파일 저장 경로. None이면 open() 시점에 세션 파일명을 생성한다.
    """

    FIELDNAMES = [
        "timestamp", "cpu_temp", "fps", "tof_distance_cm", "alert_triggered",
        "latency_ms", "tts_spoken", "occlusion_alerts",
        "alerts_emitted", "tof_raw_cm", "tof_only_ratio",
        "frame_luma", "no_detect_ratio", "mid_suppressed",
        "exposure", "gain",
    ]

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path
        self._file = None
        self._writer: Optional[csv.DictWriter] = None
        self._unflushed_rows = 0

    @property
    def path(self) -> Optional[str]:
        """현재 기록 중인 CSV 경로. open() 전에는 생성자 인자값(또는 None)."""
        return self._path

    def open(self) -> None:
        """CSV 파일을 열고 헤더를 작성한다. 중간 디렉터리가 없으면 자동 생성한다.

        경로를 지정하지 않았으면 `<LOG_DIR>/<LOG_FILE_PREFIX>_YYYYmmdd_HHMMSS.csv`
        형식으로 세션 파일을 만든다.

        Raises:
            RuntimeError: 이미 열려 있을 때.
        """
        if self._file is not None:
            raise RuntimeError("CsvLogger가 이미 열려 있습니다. close() 후 재호출하세요.")
        if self._path is None:
            self._path = self._new_session_path()
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()
        logger.info("CsvLogger 시작: %s", self._path)

    @staticmethod
    def _new_session_path() -> str:
        """기존 파일과 충돌하지 않는 세션 CSV 경로를 만든다.

        Orange Pi 5에는 RTC 배터리가 없어 부팅 시 시계가 과거로 되감긴다
        (2026-07-28 관측: 12:25:03으로 복원됐다 NTP가 17:24:30으로 5시간 점프).
        따라서 타임스탬프만으로는 파일명이 충돌할 수 있으므로, 이미 존재하면
        일련번호를 붙여 **기존 파일을 절대 덮어쓰지 않는다.**
        """
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = Path(config.LOG_DIR) / f"{config.LOG_FILE_PREFIX}_{stamp}"
        candidate = base.with_suffix(".csv")
        serial = 2
        while candidate.exists():
            candidate = base.with_name(f"{base.name}_{serial}").with_suffix(".csv")
            serial += 1
        return str(candidate)

    def write_row(
        self,
        tof_distance_cm: float,
        alert_triggered: bool,
        fps: int,
        cpu_temp: float = 0.0,
        latency_ms: float = 0.0,
        tts_spoken: str = "",
        occlusion_alerts: int = 0,
        alerts_emitted: int = 0,
        tof_raw_cm: float = 0.0,
        tof_only_ratio: float = 0.0,
        frame_luma: Optional[float] = None,
        no_detect_ratio: float = 0.0,
        mid_suppressed: int = 0,
        exposure: Optional[int] = None,
        gain: Optional[int] = None,
    ) -> None:
        """현재 운영 데이터를 한 행으로 기록한다.

        Args:
            tof_distance_cm: 퓨전 엔진이 반환한 필터링된 ToF 거리 (cm).
            alert_triggered: 이 사이클에서 경보가 발생했는지 여부.
            fps: 현재 실측 FPS (반올림 정수).
            cpu_temp: CPU 온도 (°C). 기본값 0.0 (측정 불가 환경).
            latency_ms: E2E 레이턴시 EMA (ms). 기본값 0.0.
            tts_spoken: 이 로그 주기에 발화된 TTS 텍스트. 기본값 "" (발화 없음).
            occlusion_alerts: 이 로그 주기에 발생한 카메라 가림 경고 횟수 (누적 총계가 아닌 증가분).
            alerts_emitted: 이 로그 주기에 경보 정책이 실제로 통과시킨 경보 횟수 (증가분).
            tof_raw_cm: 필터 이전 ToF 원시 거리 (cm). OoR 비율·노이즈 사후 분석용.
            tof_only_ratio: 이 로그 주기에서 ToF 단독 모드였던 사이클 비율 (0.0~1.0).
                비전이 실명한 정도를 나타내며, 경보 감소가 개선인지 실명인지 구별하는 데 쓴다.
            frame_luma: 마지막 프레임의 평균 휘도 (0~255). 노출 건강도 지표로,
                화이트아웃/암흑 프레임 비율을 사후 산출하는 근거다. 측광 불가 시 None.
            no_detect_ratio: 이 로그 주기에서 탐지가 0개였던 사이클 비율 (0.0~1.0).
                tof_only_ratio가 실명 전용이 되면서 빠진 "탐지 밀도"를 대신 담는다.
            mid_suppressed: 비전이 정상인데 탐지가 없어 MID 경보를 억제한 횟수 (증가분).
                억제로 놓친 장애물이 있었는지 사후 검증할 유일한 수단이다.
            exposure: 현재 센서 노출값 (v4l2 `exposure`). 노출 제어가 없으면 None.
                `CSI_AE_EXPOSURE_MAX`(모션블러 상한)에 붙어 있었는지를 사후에 알아야
                블러의 원인이 노출 시간인지 보행 중 움직임인지 가릴 수 있다.
            gain: 현재 센서 아날로그 게인 (v4l2 `analogue_gain`). 없으면 None.
                노출 상한을 낮춘 대가로 게인(노이즈)이 얼마나 올라갔는지 본다.

        Raises:
            RuntimeError: open() 미호출 시.
        """
        if self._writer is None:
            raise RuntimeError("open()을 먼저 호출하세요.")
        self._writer.writerow(
            {
                "timestamp": datetime.datetime.now().isoformat(timespec="milliseconds"),
                "cpu_temp": cpu_temp,
                "fps": fps,
                "tof_distance_cm": round(tof_distance_cm, 2),
                "alert_triggered": alert_triggered,
                "latency_ms": round(latency_ms, 1),
                "tts_spoken": tts_spoken,
                "occlusion_alerts": occlusion_alerts,
                "alerts_emitted": alerts_emitted,
                "tof_raw_cm": round(tof_raw_cm, 2),
                "tof_only_ratio": round(tof_only_ratio, 3),
                "frame_luma": "" if frame_luma is None else round(frame_luma, 1),
                "no_detect_ratio": round(no_detect_ratio, 3),
                "mid_suppressed": mid_suppressed,
                "exposure": "" if exposure is None else exposure,
                "gain": "" if gain is None else gain,
            }
        )
        self._unflushed_rows += 1
        if self._unflushed_rows >= config.LOG_FLUSH_INTERVAL_ROWS:
            self._file.flush()
            self._unflushed_rows = 0

    def close(self) -> None:
        """버퍼링된 행을 디스크에 반영하고 CSV 파일을 닫는다."""
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None
            self._writer = None
            self._unflushed_rows = 0
            logger.info("CsvLogger 종료: %s", self._path)
