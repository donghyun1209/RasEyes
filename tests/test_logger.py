"""CsvLogger 테스트."""
import csv
import datetime
from pathlib import Path

import pytest

import config
import logs.logger as logger_module
from logs.logger import CsvLogger


class TestCsvLogger:
    def test_schema_has_required_fields(self, tmp_path) -> None:
        """CSV 헤더가 정해진 스키마와 일치한다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == CsvLogger.FIELDNAMES

    def test_write_row_appears_in_file(self, tmp_path) -> None:
        """write_row 호출 후 파일에 1행이 기록된다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.write_row(tof_distance_cm=120.5, alert_triggered=True, fps=15)
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert float(rows[0]["tof_distance_cm"]) == pytest.approx(120.5)
        assert rows[0]["alert_triggered"] == "True"

    def test_multiple_rows_in_order(self, tmp_path) -> None:
        """복수의 write_row 호출이 순서대로 기록된다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.write_row(tof_distance_cm=80.0, alert_triggered=True, fps=15)
        log.write_row(tof_distance_cm=160.0, alert_triggered=False, fps=15)
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert float(rows[0]["tof_distance_cm"]) == pytest.approx(80.0)
        assert float(rows[1]["tof_distance_cm"]) == pytest.approx(160.0)

    def test_write_before_open_raises(self, tmp_path) -> None:
        """open() 전 write_row 호출 시 RuntimeError를 발생시킨다."""
        log = CsvLogger(path=str(tmp_path / "test.csv"))
        with pytest.raises(RuntimeError):
            log.write_row(tof_distance_cm=100.0, alert_triggered=False, fps=15)

    def test_open_twice_raises(self, tmp_path) -> None:
        """이미 열린 로거에 open() 재호출 시 RuntimeError를 발생시킨다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        with pytest.raises(RuntimeError):
            log.open()
        log.close()

    def test_creates_parent_directories(self, tmp_path) -> None:
        """중간 디렉터리가 없어도 자동 생성된다."""
        path = str(tmp_path / "nested" / "dir" / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.close()
        assert Path(path).exists()

    def test_cpu_temp_default_and_fps_explicit(self, tmp_path) -> None:
        """cpu_temp=0.0 기본값 및 명시적 fps 값이 정확히 기록된다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.write_row(tof_distance_cm=200.0, alert_triggered=False, fps=config.TARGET_FPS)
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert float(rows[0]["cpu_temp"]) == pytest.approx(0.0)
        assert int(rows[0]["fps"]) == config.TARGET_FPS

    def test_alert_not_triggered_recorded(self, tmp_path) -> None:
        """alert_triggered=False가 'False' 문자열로 기록된다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.write_row(tof_distance_cm=200.0, alert_triggered=False, fps=15)
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["alert_triggered"] == "False"

    def test_occlusion_alerts_default_and_explicit(self, tmp_path) -> None:
        """occlusion_alerts가 기본값 0 및 명시값으로 기록된다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.write_row(tof_distance_cm=200.0, alert_triggered=False, fps=15)
        log.write_row(tof_distance_cm=200.0, alert_triggered=False, fps=15, occlusion_alerts=2)
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert int(rows[0]["occlusion_alerts"]) == 0
        assert int(rows[1]["occlusion_alerts"]) == 2

    def test_timestamp_is_present(self, tmp_path) -> None:
        """timestamp 컬럼이 비어 있지 않게 기록된다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.write_row(tof_distance_cm=50.0, alert_triggered=True, fps=12)
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["timestamp"] != ""

    def test_diagnostic_columns_recorded(self, tmp_path) -> None:
        """진단 컬럼(alerts_emitted, tof_raw_cm, tof_only_ratio)이 기록된다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.write_row(tof_distance_cm=90.0, alert_triggered=True, fps=15)
        log.write_row(
            tof_distance_cm=90.0,
            alert_triggered=True,
            fps=15,
            alerts_emitted=3,
            tof_raw_cm=87.456,
            tof_only_ratio=0.8234,
        )
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert int(rows[0]["alerts_emitted"]) == 0
        assert float(rows[0]["tof_only_ratio"]) == pytest.approx(0.0)
        assert int(rows[1]["alerts_emitted"]) == 3
        assert float(rows[1]["tof_raw_cm"]) == pytest.approx(87.46)
        assert float(rows[1]["tof_only_ratio"]) == pytest.approx(0.823)


class TestSessionFile:
    """세션별 CSV 파일 생성 — 재시작 시 이전 세션을 덮어쓰지 않아야 한다."""

    def test_session_path_generated_when_path_omitted(self, tmp_path, monkeypatch) -> None:
        """path 인자를 생략하면 LOG_DIR 아래에 타임스탬프 파일명을 만든다."""
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
        log = CsvLogger()
        assert log.path is None

        log.open()
        log.close()

        created = Path(log.path)
        assert created.exists()
        assert created.parent == tmp_path
        assert created.name.startswith(f"{config.LOG_FILE_PREFIX}_")
        assert created.suffix == ".csv"

    def test_existing_file_is_never_overwritten(self, tmp_path, monkeypatch) -> None:
        """같은 타임스탬프의 파일이 이미 있으면 일련번호를 붙인다.

        Orange Pi 5는 RTC 배터리가 없어 부팅 시 시계가 되감긴다. 파일명이
        충돌해도 기존 세션 로그가 사라지면 안 된다.
        """
        monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))

        first = CsvLogger()
        first.open()
        first.write_row(tof_distance_cm=11.0, alert_triggered=True, fps=15)
        first.close()

        # 시계가 되감겨 동일한 타임스탬프가 나오는 상황을 강제한다
        stamp = Path(first.path).stem.replace(f"{config.LOG_FILE_PREFIX}_", "")

        class _FrozenDatetime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.datetime.strptime(stamp, "%Y%m%d_%H%M%S")

        monkeypatch.setattr(logger_module.datetime, "datetime", _FrozenDatetime)

        second = CsvLogger()
        second.open()
        second.write_row(tof_distance_cm=22.0, alert_triggered=False, fps=15)
        second.close()

        assert second.path != first.path, "충돌한 파일명을 그대로 재사용했다"

        # 첫 세션 내용이 보존되어야 한다
        with open(first.path, newline="", encoding="utf-8") as f:
            first_rows = list(csv.DictReader(f))
        assert len(first_rows) == 1
        assert float(first_rows[0]["tof_distance_cm"]) == pytest.approx(11.0)

        with open(second.path, newline="", encoding="utf-8") as f:
            second_rows = list(csv.DictReader(f))
        assert float(second_rows[0]["tof_distance_cm"]) == pytest.approx(22.0)

    def test_explicit_path_still_honoured(self, tmp_path) -> None:
        """경로를 명시하면 세션 파일명을 만들지 않고 그대로 쓴다."""
        path = str(tmp_path / "explicit.csv")
        log = CsvLogger(path=path)
        log.open()
        log.close()
        assert log.path == path
        assert Path(path).exists()
