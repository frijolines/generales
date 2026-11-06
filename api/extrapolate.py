# -*- coding: utf-8 -*-
"""
api/extrapolate.py
==================
Endpoint Serverless Python para Vercel que expone el motor matemático `engine.py`
vía HTTP (GET /api/extrapolate devuelve la extrapolación base y POST /api/extrapolate
ejecuta la extrapolación sobre las 428 mesas de Álava con los votos enviados).
"""

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import engine  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_GET(self):
        df = engine.load_historical_dataframe()
        st_live = engine.load_or_init_live_state(df)
        res = engine.extrapolate_alava(
            df_historico=df,
            votos_29N=st_live.get("votos_29N", {}),
            modo_horario=st_live.get("modo_horario", "20:00h Escrutinio"),
            avances_29N=st_live.get("avances_29N", {}),
            tau=0.05,
        )
        res_clean = {k: v for k, v in res.items() if not hasattr(v, "to_dict")}
        res_clean["tabla_barrios_vitoria"] = res["tabla_barrios_vitoria"].to_dict(orient="records")
        res_clean["tabla_cuadrillas_alava"] = res["tabla_cuadrillas_alava"].to_dict(orient="records")
        res_clean["tabla_municipios_alava"] = res["tabla_municipios_alava"].to_dict(orient="records")
        self._send_json(200, res_clean)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        body = json.loads(raw or "{}")
        df = engine.load_historical_dataframe()
        votos_29N = body.get("votos_29N", {})
        modo_horario = body.get("modo_horario", "20:00h Escrutinio")
        avances_29N = body.get("avances_29N", {})
        res = engine.extrapolate_alava(
            df_historico=df,
            votos_29N=votos_29N,
            modo_horario=modo_horario,
            avances_29N=avances_29N,
            tau=0.05,
        )
        res_clean = {k: v for k, v in res.items() if not hasattr(v, "to_dict")}
        res_clean["tabla_barrios_vitoria"] = res["tabla_barrios_vitoria"].to_dict(orient="records")
        res_clean["tabla_cuadrillas_alava"] = res["tabla_cuadrillas_alava"].to_dict(orient="records")
        res_clean["tabla_municipios_alava"] = res["tabla_municipios_alava"].to_dict(orient="records")
        self._send_json(200, res_clean)

