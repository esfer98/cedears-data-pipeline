import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import db  # noqa: E402


@pytest.fixture
def con(tmp_path):
    """Warehouse vacio en un archivo temporal: los tests nunca tocan data/warehouse.duckdb."""
    conexion = db.conectar(tmp_path / "test.duckdb")
    yield conexion
    conexion.close()


def insertar(con, tabla: str, filas: list[dict]) -> None:
    """Inserta filas parciales: las columnas que no se pasan quedan NULL."""
    for fila in filas:
        cols = ", ".join(fila)
        marcas = ", ".join("?" for _ in fila)
        con.execute(f"INSERT INTO {tabla} ({cols}) VALUES ({marcas})", list(fila.values()))
