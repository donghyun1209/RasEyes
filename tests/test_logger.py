"""CsvLogger 테스트."""
import csv
from pathlib import Path

import pytest

import config
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
        log.write_row(tof_distance_cm=120.5, alert_triggered=True)
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
        log.write_row(tof_distance_cm=80.0, alert_triggered=True)
        log.write_row(tof_distance_cm=160.0, alert_triggered=False)
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
            log.write_row(tof_distance_cm=100.0, alert_triggered=False)

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

    def test_cpu_temp_and_fps_defaults(self, tmp_path) -> None:
        """cpu_temp=0.0, fps=TARGET_FPS가 기본값으로 기록된다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.write_row(tof_distance_cm=200.0, alert_triggered=False)
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
        log.write_row(tof_distance_cm=200.0, alert_triggered=False)
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["alert_triggered"] == "False"

    def test_timestamp_is_present(self, tmp_path) -> None:
        """timestamp 컬럼이 비어 있지 않게 기록된다."""
        path = str(tmp_path / "test.csv")
        log = CsvLogger(path=path)
        log.open()
        log.write_row(tof_distance_cm=50.0, alert_triggered=True)
        log.close()

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["timestamp"] != ""
