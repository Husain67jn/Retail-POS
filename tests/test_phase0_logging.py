from pathlib import Path

from app.utils.logger import configure_logging


def test_logging_writes_file(tmp_path: Path):
    logger = configure_logging(tmp_path)
    logger.info("phase0 logging test")
    for handler in logger.handlers:
        handler.flush()

    log_file = tmp_path / "app.log"
    assert log_file.exists()
    assert "phase0 logging test" in log_file.read_text(encoding="utf-8")
