"""Loop de main: la base caida no termina el proceso y el heartbeat solo se
toca cuando la cola responde. Sin base real: la conexion, el DDL y la cola se
parchean, y time.sleep corta el loop cuando ya se vio lo que importa."""
from unittest import mock

import pytest
from sqlalchemy.exc import OperationalError

import main


class _Stop(Exception):
    """Lo lanza el sleep falso para salir del while True."""


@pytest.fixture
def loop(monkeypatch):
    monkeypatch.setattr(main, "get_connection_querylens_db", mock.MagicMock())
    monkeypatch.setattr(main, "ensure_hallazgos_table", mock.MagicMock())
    monkeypatch.setattr(main, "ensure_statement_samples_table", mock.MagicMock())
    sleeps = []

    def run(reads, stop_after_sleeps):
        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= stop_after_sleeps:
                raise _Stop

        monkeypatch.setattr(main, "read_next_job", mock.MagicMock(side_effect=reads))
        monkeypatch.setattr(main.time, "sleep", fake_sleep)
        with pytest.raises(_Stop):
            main.main()
        return sleeps

    return run


def _caida():
    return OperationalError("SELECT * FROM pgmq.read", {}, Exception("server closed the connection"))


def test_base_caida_reintenta_con_backoff_en_vez_de_terminar(loop):
    sleeps = loop([_caida(), _caida(), _caida(), None], stop_after_sleeps=4)

    # 5 -> 10 -> 20 y, con la base de vuelta, la espera normal de la cola vacia
    assert sleeps == [5, 10, 20, main.POLL_INTERVAL_SECONDS]


def test_el_backoff_tiene_tope(loop):
    sleeps = loop([_caida()] * 6, stop_after_sleeps=6)

    assert sleeps == [5, 10, 20, 40, 60, 60]


def test_heartbeat_solo_cuando_la_cola_responde(loop, monkeypatch, tmp_path):
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setenv("HEARTBEAT_FILE", str(heartbeat))

    loop([_caida()], stop_after_sleeps=1)
    assert not heartbeat.exists(), "con la base caida no hay latido"

    loop([None], stop_after_sleeps=1)
    assert heartbeat.exists()


def test_oneshot_no_oculta_la_base_caida(monkeypatch):
    monkeypatch.setattr(main, "get_connection_querylens_db", mock.MagicMock())
    monkeypatch.setattr(main, "ensure_hallazgos_table", mock.MagicMock())
    monkeypatch.setattr(main, "ensure_statement_samples_table", mock.MagicMock())
    monkeypatch.setattr(main, "read_next_job", mock.MagicMock(side_effect=_caida()))

    with pytest.raises(OperationalError):
        main.main(oneshot=True)


def test_beat_sin_heartbeat_file_no_escribe(monkeypatch, tmp_path):
    monkeypatch.delenv("HEARTBEAT_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    main.beat()

    assert list(tmp_path.iterdir()) == []
