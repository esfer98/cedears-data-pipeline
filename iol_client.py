"""
iol_client.py
-------------
Cliente para la API de InvertirOnline (IOL): real-time + historicos +
portafolio + (eventualmente) ejecucion de ordenes.

AUTENTICACION (OAuth2 password grant, tokens digitales):
  POST /token  grant_type=password        -> access_token (vale 15 min) + refresh_token
  POST /token  grant_type=refresh_token   -> renueva el access_token sin re-loguear

CREDENCIALES: NUNCA hardcodear usuario/clave en el codigo.
  Se leen de variables de entorno:
    export IOL_USERNAME="tu_usuario"
    export IOL_PASSWORD="tu_password"

OJO: la API de IOL opera sobre el entorno REAL. Para probar ordenes hay un
     sandbox aparte. Consultar cotizaciones/portafolio es de solo lectura y seguro.

Requiere: pip install requests pandas
"""

import os
import time
import requests

BASE = "https://api.invertironline.com"


class IOLClient:
    def __init__(self, username: str | None = None, password: str | None = None):
        self.username = username or os.environ["IOL_USERNAME"]
        self.password = password or os.environ["IOL_PASSWORD"]
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self.session = requests.Session()

    # ---------------- auth ----------------
    def _request_token(self, data: dict) -> dict:
        r = self.session.post(
            f"{BASE}/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        r.raise_for_status()
        tok = r.json()
        self._access_token = tok["access_token"]
        self._refresh_token = tok.get("refresh_token", self._refresh_token)
        # expires_in viene en segundos; dejamos 60s de colchon
        self._expires_at = time.time() + int(tok.get("expires_in", 900)) - 60
        return tok

    def login(self) -> dict:
        return self._request_token({
            "username": self.username,
            "password": self.password,
            "grant_type": "password",
        })

    def _refresh(self) -> dict:
        return self._request_token({
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        })

    def _auth_header(self) -> dict:
        """Devuelve el header Bearer, renovando el token si hace falta."""
        if self._access_token is None:
            self.login()
        elif time.time() >= self._expires_at:
            try:
                self._refresh()
            except Exception:
                self.login()  # si el refresh fallo/expiro, re-login completo
        return {"Authorization": f"Bearer {self._access_token}"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self.session.get(
            f"{BASE}{path}", headers=self._auth_header(), params=params, timeout=30
        )
        if not r.ok:
            # IOL suele devolver un JSON/texto explicando por que rechazo el request.
            # Sin esto, un 400 no dice nada util.
            raise requests.HTTPError(
                f"{r.status_code} en {path}\nRespuesta de IOL: {r.text}",
                response=r,
            )
        return r.json()

    # ---------------- market data ----------------
    def cedears_panel(self, instrumento: str = "Acciones", pais: str = "argentina") -> dict:
        """Panel completo de CEDEARs: universo investible + cotizaciones en vivo.

        CEDEARs no es un instrumento en si (ver /api/v2/{pais}/Titulos/Cotizacion/Instrumentos),
        sino un PANEL dentro del instrumento 'Acciones' para pais 'argentina'
        (ver /api/v2/argentina/Titulos/Cotizacion/Paneles/Acciones).
        """
        return self._get(f"/api/v2/Cotizaciones/{instrumento}/CEDEARs/{pais}")

    def quote(self, simbolo: str, mercado: str = "bCBA") -> dict:
        """Cotizacion puntual de un titulo (mercado bCBA = BYMA/Bolsa de Bs. As.)."""
        return self._get(f"/api/v2/{mercado}/Titulos/{simbolo}/Cotizacion")

    def historical(
        self,
        simbolo: str,
        desde: str,
        hasta: str,
        ajustada: str = "ajustada",
        mercado: str = "bCBA",
    ) -> list:
        """
        Serie historica OHLC. Fechas 'YYYY-MM-DD'.
        ajustada: 'ajustada' (recomendado) o 'sinAjustar'.
        Para CEDEARs, 'ajustada' corrige splits/cambios de ratio -> usala para retornos.
        """
        path = (
            f"/api/v2/{mercado}/Titulos/{simbolo}/Cotizacion/"
            f"seriehistorica/{desde}/{hasta}/{ajustada}"
        )
        return self._get(path)

    # ---------------- cuenta ----------------
    def portafolio(self, pais: str = "argentina") -> dict:
        return self._get(f"/api/v2/portafolio/{pais}")

    def estado_cuenta(self) -> dict:
        return self._get("/api/v2/estadocuenta")


if __name__ == "__main__":
    import pandas as pd

    iol = IOLClient()
    iol.login()
    print("Login OK")

    panel = iol.cedears_panel()
    # El panel suele venir como {"titulos": [...]}; confirma la key en la 1ra corrida
    titulos = panel.get("titulos", panel) if isinstance(panel, dict) else panel
    df = pd.DataFrame(titulos)

    print(f"\nCEDEARs en el panel: {len(df)}")
    print("Campos disponibles:", list(df.columns))
    print(df.head(10))

    # Ejemplo de serie historica (descomenta para probar):
    # serie = iol.historical("AAPL", "2026-01-01", "2026-07-01", "ajustada")
    # print(pd.DataFrame(serie).tail())
