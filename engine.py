# -*- coding: utf-8 -*-
"""
engine.py
=========
Motor Matemático Único de Quick Count y Extrapolación Electoral en Tiempo Real
sobre las 428 mesas electorales del Territorio Histórico de Álava / Araba (29N).

Implementa:
1. Detección automática de N mesas activas (votos > 0) y semáforo de validación censal.
2. Cálculo de Voto Válido (excluyendo nulos del denominador) y vector de Swing (29N vs 23J).
3. Matriz de Similitud Coseno entre las (428 - N) mesas vacías y las N mesas activas,
   ponderada mediante Softmax con temperatura (tau = 0.05):
       w_{m,k} = exp(sim(h_m, h_k) / 0.05) / sum_{j=1}^N exp(sim(h_m, h_j) / 0.05)
4. Imputación de Swing con factor de amortiguación bayesiana gamma_N = min(1.0, 0.5 + 0.125 * N),
   truncamiento de negativos p_tilde = max(0, p_2023 + delta_hat), renormalización al 100%
   y proyección de votos por mesa según censo y participación proyectada.
5. Reparto directo de los 4 Escaños de Álava mediante Ley D'Hondt (Art. 163 LOREG, barrera 3%
   sobre voto válido total provincial) y cálculo exacto al voto de la disputa entre el 4º y 5º escaño.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from sklearn.metrics.pairwise import cosine_similarity as sk_cosine_similarity
except ImportError:
    sk_cosine_similarity = None

BASE_DIR = Path(__file__).resolve().parent
CENSO_CSV_PATH = BASE_DIR / "censo_alava_2023.csv"
ESTADO_VIVO_JSON_PATH = BASE_DIR / "estado_vivo_29N.json"

# Opciones de voto y filas de la matriz de picado rápido
OPCIONES_VALIDAS = [
    "PSE-EE",
    "EH Bildu",
    "PP",
    "EAJ-PNV",
    "Sumar/Izq",
    "Vox",
    "Otros",
    "Blancos",
]
FILAS_MATRIZ = OPCIONES_VALIDAS + ["Nulos"]
PARTIDOS_ESCANOS = ["PSE-EE", "EH Bildu", "PP", "EAJ-PNV", "Sumar/Izq", "Vox"]

# Paleta corporativa oficial de partidos vascos / estatales
COLORES_PARTIDOS = {
    "PSE-EE": "#E30613",     # Rojo PSE-EE/PSOE
    "EH Bildu": "#00A88F",   # Verde turquesa EH Bildu
    "PP": "#005999",         # Azul PP
    "EAJ-PNV": "#008037",    # Verde jeltzale EAJ-PNV
    "Sumar/Izq": "#E51C55",  # Magenta Sumar
    "Vox": "#63BE21",        # Verde claro Vox
    "Otros": "#8C969E",      # Gris Otros
    "Blancos": "#CBD5E1",    # Gris claro Blancos
}

ORDEN_CUADRILLAS = [
    "Vitoria-Gasteiz",
    "Ayala/Aiara - Laudio y Amurrio",
    "Llanada Alavesa",
    "Rioja Alavesa",
    "Gorbeialdea",
    "Añana",
    "Montaña Alavesa",
]


def compute_cosine_similarity(X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """
    Calcula la matriz de Similitud Coseno entre las filas de X (M x D) y de Y (N x D).
    Usa scikit-learn si está disponible, o álgebra vectorial NumPy equivalente.
    """
    if sk_cosine_similarity is not None:
        return sk_cosine_similarity(X, Y)
    x_norm = np.linalg.norm(X, axis=1, keepdims=True)
    y_norm = np.linalg.norm(Y, axis=1, keepdims=True)
    x_norm = np.where(x_norm == 0.0, 1e-12, x_norm)
    y_norm = np.where(y_norm == 0.0, 1e-12, y_norm)
    return (X / x_norm) @ (Y / y_norm).T


def load_historical_dataframe(csv_path: Path = CENSO_CSV_PATH) -> pd.DataFrame:
    """Carga el DataFrame único con las 428 mesas electorales de Álava (base 23J 2023)."""
    if not csv_path.exists():
        from ingest_and_validate import process_alava_census
        process_alava_census()
    df = pd.read_csv(csv_path, encoding="utf-8", dtype={"provincia": str, "cod_municipio": str, "distrito": str, "seccion": str})
    df["alias_testigo"] = df["alias_testigo"].fillna("")
    return df


def build_default_witness_state(df: pd.DataFrame, scenario: str = "29N_COMPETITIVO") -> Dict[str, Any]:
    """
    Construye el estado inicial de `estado_vivo_29N.json` con las 4 mesas testigo
    preseleccionadas de Vitoria-Gasteiz (Mesa A, Mesa B, Mesa C, Mesa D).
    - Si scenario == "29N_COMPETITIVO": inicia con un escenario en vivo realista 29N
      donde EH Bildu, PSE-EE, PP y EAJ-PNV disputan voto a voto los 4 escaños de Álava.
    - Si scenario == "23J_EXACTO": carga los votos exactos de julio de 2023 en las 4 mesas.
    - Si scenario == "VACIO": inicia las 4 mesas a 0 votos.
    """
    testigos_df = df[df["es_testigo_inicial"] == 1].sort_values("alias_testigo")
    mesas_seleccionadas = list(testigos_df["id_mesa"].values)

    votos_mesas: Dict[str, Dict[str, int]] = {}
    avances_mesas: Dict[str, Dict[str, float]] = {}

    # Factores de simulación realista 29N (ligero ascenso EH Bildu y PSE, pugna cerrada PNV/PP/PSE por el 4º escaño)
    mult_29n = {
        "PSE-EE": 1.01,
        "EH Bildu": 1.07,
        "PP": 0.99,
        "EAJ-PNV": 0.97,
        "Sumar/Izq": 0.91,
        "Vox": 0.96,
        "Otros": 0.95,
        "Blancos": 1.00,
        "Nulos": 1.00,
    }

    for _, row in df.iterrows():
        m_id = str(row["id_mesa"])
        avances_mesas[m_id] = {
            "avance_14h": float(row["avance_14h_2023"]),
            "avance_18h": float(row["avance_18h_2023"]),
        }
        if m_id in mesas_seleccionadas:
            if scenario == "VACIO":
                votos_mesas[m_id] = {f: 0 for f in FILAS_MATRIZ}
            elif scenario == "23J_EXACTO":
                votos_mesas[m_id] = {f: int(row[f"{f}_2023"]) for f in FILAS_MATRIZ}
            else:
                votos_mesas[m_id] = {
                    f: max(0, int(round(int(row[f"{f}_2023"]) * mult_29n[f])))
                    for f in FILAS_MATRIZ
                }

    return {
        "modo_horario": "20:00h Escrutinio",
        "mesas_en_cola": mesas_seleccionadas,
        "votos_29N": votos_mesas,
        "avances_29N": avances_mesas,
        "escenario_activo": scenario,
    }


def load_or_init_live_state(df: pd.DataFrame, json_path: Path = ESTADO_VIVO_JSON_PATH) -> Dict[str, Any]:
    """Carga el estado vivo desde `estado_vivo_29N.json` o lo inicializa con las 4 mesas testigo."""
    if json_path.exists():
        try:
            with open(json_path, mode="r", encoding="utf-8") as f:
                data = json.load(f)
            if "mesas_en_cola" in data and "votos_29N" in data:
                return data
        except Exception:
            pass
    default_state = build_default_witness_state(df, scenario="29N_COMPETITIVO")
    save_live_state(default_state, json_path)
    return default_state


def save_live_state(state: Dict[str, Any], json_path: Path = ESTADO_VIVO_JSON_PATH) -> None:
    """Guarda instantáneamente el estado de picado en `estado_vivo_29N.json`."""
    with open(json_path, mode="w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def validate_mesa_censo(
    censo: int, votos_dict: Dict[str, int]
) -> Tuple[str, str, int, int, float]:
    """
    Evalúa el semáforo de validación censal de una mesa en la consola de picado:
    Devuelve: (codigo_estado, texto_semaforo, votos_validos, votos_totales, participacion_pct)
    """
    v_validos = sum(int(votos_dict.get(p, 0) or 0) for p in OPCIONES_VALIDAS)
    v_nulos = int(votos_dict.get("Nulos", 0) or 0)
    v_totales = v_validos + v_nulos
    part_pct = (v_totales / float(censo) * 100.0) if censo > 0 else 0.0

    if v_totales <= 0:
        return ("ESPERA", "⚪ En espera (0 votos)", 0, 0, 0.0)
    if v_totales > censo:
        exceso = v_totales - censo
        return (
            "ERROR_CENSO",
            f"🔴 ERROR: Supera censo (+{exceso} votos)",
            v_validos,
            v_totales,
            part_pct,
        )
    if v_validos == 0 and v_nulos > 0:
        return (
            "ALERTA_NULOS",
            "🟡 Alerta: Solo votos nulos (sin voto válido)",
            v_validos,
            v_totales,
            part_pct,
        )
    if part_pct > 92.0:
        return (
            "ALERTA_ALTA",
            f"🟡 Activa · Part. muy alta ({part_pct:.1f}%)",
            v_validos,
            v_totales,
            part_pct,
        )
    return (
        "ACTIVA_OK",
        f"🟢 Activa y validada ({part_pct:.1f}% part.)",
        v_validos,
        v_totales,
        part_pct,
    )


def compute_dhondt_alava(
    votos_partidos: Dict[str, float],
    total_votos_validos: float,
    num_escanos: int = 4,
    barrera_pct: float = 3.0,
) -> Dict[str, Any]:
    """
    Aplica la Ley D'Hondt directa (Art. 163 LOREG) para asignar los 4 escaños de Álava:
    1. Filtra candidaturas con >= 3% del Voto Válido Total provincial (candidaturas + blancos).
    2. Divide los votos de cada candidatura entre 1, 2, 3, 4.
    3. Ordena los cocientes de mayor a menor (desempate por votos totales y sorteo).
    4. Calcula exactamente qué partido gana el 4º escaño, quién es el 5º (primer excluido)
       y cuántos votos exactos en toda Álava los separan.
    """
    umbral_votos = (barrera_pct / 100.0) * float(total_votos_validos)
    partidos_aptos = {}
    partidos_excluidos_barrera = []

    for p in PARTIDOS_ESCANOS:
        v_p = float(votos_partidos.get(p, 0.0))
        if v_p >= umbral_votos and v_p > 0:
            partidos_aptos[p] = v_p
        else:
            partidos_excluidos_barrera.append(p)

    # Generar todos los cocientes D'Hondt (divisores 1..num_escanos)
    todos_cocientes = []
    tabla_cocientes: Dict[str, List[float]] = {}
    for p in PARTIDOS_ESCANOS:
        v_p = float(votos_partidos.get(p, 0.0))
        tabla_cocientes[p] = [v_p / float(d) for d in range(1, num_escanos + 1)]
        if p in partidos_aptos:
            for d in range(1, num_escanos + 1):
                todos_cocientes.append(
                    {
                        "partido": p,
                        "divisor": d,
                        "cociente": v_p / float(d),
                        "votos_totales": v_p,
                    }
                )

    # Ordenar por cociente descendente, con desempate por votos totales de la candidatura (Art. 163.1.d LOREG)
    todos_cocientes.sort(
        key=lambda x: (round(x["cociente"], 6), round(x["votos_totales"], 4)),
        reverse=True,
    )

    escanos_por_partido = {p: 0 for p in PARTIDOS_ESCANOS}
    asignaciones_ordenadas = []
    for idx, item in enumerate(todos_cocientes[:num_escanos]):
        p = item["partido"]
        escanos_por_partido[p] += 1
        asignaciones_ordenadas.append(
            {
                "orden_escano": idx + 1,
                "partido": p,
                "divisor": item["divisor"],
                "cociente": item["cociente"],
                "votos_totales": item["votos_totales"],
            }
        )

    # Análisis del 4º escaño (último asignado) vs 5º cociente (primer aspirante sin ese escaño)
    cuarto_escano = todos_cocientes[num_escanos - 1]
    # Buscar el mejor cociente a partir de la posición 5ª que pertenezca a un partido distinto al del 4º escaño
    # (y también el 5º cociente absoluto de la tabla)
    quinto_absoluto = todos_cocientes[num_escanos]
    quinto_distinto = next(
        (c for c in todos_cocientes[num_escanos:] if c["partido"] != cuarto_escano["partido"]),
        quinto_absoluto,
    )

    # Para arrebatar el 4º escaño al partido P4 (con cociente Q4 = V4 / d4),
    # el aspirante P5 (en su divisor d5) necesita alcanzar un voto V5_nuevo tal que:
    # V5_nuevo / d5 > Q4  (o >= Q4 si V5_nuevo > V4).
    q4 = cuarto_escano["cociente"]
    v4 = cuarto_escano["votos_totales"]
    d4 = cuarto_escano["divisor"]

    p5 = quinto_distinto["partido"]
    v5 = quinto_distinto["votos_totales"]
    d5 = quinto_distinto["divisor"]
    q5 = quinto_distinto["cociente"]

    v5_objetivo = math.floor(q4 * d5) + 1
    votos_faltan_a_p5 = max(1, int(round(v5_objetivo - v5)))

    # También calculamos cuántos votos puede perder P4 antes de caer por debajo de Q5
    v4_minimo = math.floor(q5 * d4) + 1
    colchon_votos_p4 = max(1, int(round(v4 - v4_minimo)))

    return {
        "escanos": escanos_por_partido,
        "asignaciones": asignaciones_ordenadas,
        "tabla_cocientes": tabla_cocientes,
        "umbral_votos_3pct": umbral_votos,
        "partidos_excluidos_3pct": partidos_excluidos_barrera,
        "cuarto_escano": {
            "partido": cuarto_escano["partido"],
            "divisor": d4,
            "cociente": q4,
            "votos_totales": int(round(v4)),
            "colchon_votos": colchon_votos_p4,
        },
        "quinto_aspirante": {
            "partido": p5,
            "divisor": d5,
            "cociente": q5,
            "votos_totales": int(round(v5)),
            "votos_faltan_para_4to": votos_faltan_a_p5,
        },
        "ranking_cocientes_top8": todos_cocientes[:8],
    }


def extrapolate_alava(
    df_historico: pd.DataFrame,
    votos_29N: Dict[str, Dict[str, int]],
    modo_horario: str = "20:00h Escrutinio",
    avances_29N: Optional[Dict[str, Dict[str, float]]] = None,
    tau: float = 0.05,
) -> Dict[str, Any]:
    """
    Ejecuta el motor matemático completo de extrapolación sobre las 428 mesas de Álava.
    """
    df = df_historico.copy()
    num_total_mesas = len(df)
    pct_2023_cols = [f"pct_2023_{p}" for p in OPCIONES_VALIDAS]

    # 1. Identificar mesas activas (votos válidos > 0 y sin superar censo)
    active_indices: List[int] = []
    active_ids: List[str] = []
    active_pct_29N: List[np.ndarray] = []
    active_swing: List[np.ndarray] = []
    active_part_swing: List[float] = []
    active_nulos_rate: List[float] = []

    semaforos_por_mesa: Dict[str, Dict[str, Any]] = {}
    votos_reales_activas: Dict[int, Dict[str, int]] = {}

    for idx, row in df.iterrows():
        m_id = str(row["id_mesa"])
        censo_m = int(row["censo_2023"])
        v_dict = votos_29N.get(m_id, {})
        estado_cod, texto_sem, v_val, v_tot, part_m = validate_mesa_censo(censo_m, v_dict)
        semaforos_por_mesa[m_id] = {
            "codigo": estado_cod,
            "texto": texto_sem,
            "votos_validos": v_val,
            "votos_totales": v_tot,
            "participacion_pct": part_m,
            "censo": censo_m,
        }

        # Cualquier mesa con votos válidos > 0 (y dentro de su censo) se marca automáticamente como Activa
        if v_val > 0 and estado_cod in ("ACTIVA_OK", "ALERTA_ALTA"):
            active_indices.append(idx)
            active_ids.append(m_id)
            votos_reales_activas[idx] = {f: int(v_dict.get(f, 0) or 0) for f in FILAS_MATRIZ}

            vec_29n = np.array(
                [(float(v_dict.get(p, 0) or 0) / float(v_val)) * 100.0 for p in OPCIONES_VALIDAS],
                dtype=np.float64,
            )
            vec_2023 = np.array([float(row[c]) for c in pct_2023_cols], dtype=np.float64)
            delta_k = vec_29n - vec_2023

            active_pct_29N.append(vec_29n)
            active_swing.append(delta_k)
            active_part_swing.append(part_m - float(row["participacion_2023"]))
            v_nul = float(v_dict.get("Nulos", 0) or 0)
            active_nulos_rate.append((v_nul / float(v_tot)) * 100.0 if v_tot > 0 else float(row["pct_nulos_2023"]))

    N = len(active_indices)
    # Factor de amortiguación gamma_N = min(1.0, 0.5 + 0.125 * N)
    gamma_N = min(1.0, 0.5 + 0.125 * N) if N > 0 else 0.0

    # Matriz histórica H de las 428 mesas (428 x 8)
    H_all = df[pct_2023_cols].to_numpy(dtype=np.float64)

    if N > 0:
        H_active = H_all[active_indices, :]  # (N x 8)
        Delta_active = np.vstack(active_swing)  # (N x 8)
        part_swing_vec = np.array(active_part_swing, dtype=np.float64)  # (N,)

        # Similitud Coseno entre las 428 mesas y las N mesas activas -> (428 x N)
        sim_matrix = compute_cosine_similarity(H_all, H_active)

        # Pesos Softmax con temperatura tau = 0.05:
        # w_{m,k} = exp(sim(h_m, h_k) / 0.05) / sum_{j=1}^N exp(sim(h_m, h_j) / 0.05)
        scaled_logits = sim_matrix / float(tau)
        scaled_logits_shifted = scaled_logits - np.max(scaled_logits, axis=1, keepdims=True)
        exp_logits = np.exp(scaled_logits_shifted)
        W_softmax = exp_logits / np.sum(exp_logits, axis=1, keepdims=True)  # (428 x N)

        # Imputación del swing ponderado con amortiguación:
        # \hat{\Delta}_{m,P} = \gamma_N \sum_{k=1}^N w_{m,k} \Delta_{k,P}
        Delta_imputed = gamma_N * (W_softmax @ Delta_active)  # (428 x 8)

        # Truncamiento de negativos y renormalización al 100%
        P_tilde = np.maximum(0.0, H_all + Delta_imputed)
        row_sums = np.sum(P_tilde, axis=1, keepdims=True)
        row_sums = np.where(row_sums <= 0.0, 1.0, row_sums)
        P_projected_pct = (P_tilde / row_sums) * 100.0  # (428 x 8)

        # Swing de participación ponderado por mesa
        part_delta_imputed = gamma_N * (W_softmax @ part_swing_vec)
        part_projected_pct = np.clip(
            df["participacion_2023"].to_numpy(dtype=np.float64) + part_delta_imputed,
            5.0,
            98.5,
        )

        # Cobertura sociológica: media ponderada por censo de max_k sim(h_m, h_k)
        max_sim_per_mesa = np.max(sim_matrix, axis=1)
        censos_vec = df["censo_2023"].to_numpy(dtype=np.float64)
        cobertura_sociologica = float(
            np.sum(censos_vec * max_sim_per_mesa) / np.sum(censos_vec) * 100.0
        )

        # Dispersión del swing entre mesas activas para estimar margen de error (95% IC)
        if N > 1:
            swing_std = float(np.mean(np.std(Delta_active[:, :6], axis=0, ddof=1)))
        else:
            swing_std = 2.40
        fpc = math.sqrt(max(0.0, (num_total_mesas - N) / float(num_total_mesas - 1)))
        penalty_cov = 1.0 + max(0.0, (100.0 - cobertura_sociologica) / 15.0)
        margen_error_95 = round(1.96 * (swing_std / math.sqrt(N)) * fpc * penalty_cov, 2)
    else:
        # Sin mesas activas (N = 0): base histórica 23J
        W_softmax = np.zeros((num_total_mesas, 1), dtype=np.float64)
        max_sim_per_mesa = np.zeros(num_total_mesas, dtype=np.float64)
        P_projected_pct = H_all.copy()
        part_projected_pct = df["participacion_2023"].to_numpy(dtype=np.float64).copy()
        cobertura_sociologica = 0.0
        margen_error_95 = 0.0

    # Si estamos en modo de avance horario (14:00h o 18:00h) y se han modificado avances,
    # ajustamos la participación proyectada según el diferencial de avance respecto a 2023
    avances_delta_prov = 0.0
    if avances_29N and modo_horario in ("14:00h Avance", "18:00h Avance"):
        col_hist = "avance_14h_2023" if "14:00h" in modo_horario else "avance_18h_2023"
        key_live = "avance_14h" if "14:00h" in modo_horario else "avance_18h"
        deltas_av = []
        for m_id, av_dict in avances_29N.items():
            if key_live in av_dict:
                row_m = df[df["id_mesa"] == m_id]
                if not row_m.empty:
                    val_live = float(av_dict[key_live])
                    val_hist = float(row_m[col_hist].values[0])
                    if abs(val_live - val_hist) > 0.01:
                        deltas_av.append(val_live - val_hist)
        if deltas_av:
            avances_delta_prov = float(np.mean(deltas_av))
            factor_expansion = 1.25 if "14:00h" in modo_horario else 1.12
            part_projected_pct = np.clip(
                part_projected_pct + avances_delta_prov * factor_expansion,
                5.0,
                98.5,
            )

    # 2. Construir el DataFrame extrapolado de las 428 mesas (reales en N activas + proyectadas en 428 - N)
    df["es_activa"] = 0
    df["similitud_max_testigo"] = np.round(max_sim_per_mesa * 100.0, 2)
    df["participacion_29N"] = np.round(part_projected_pct, 2)

    censos_arr = df["censo_2023"].to_numpy(dtype=np.float64)
    pct_nulos_arr = df["pct_nulos_2023"].to_numpy(dtype=np.float64)

    votos_totales_29N = np.round(censos_arr * (part_projected_pct / 100.0)).astype(int)
    nulos_29N = np.round(votos_totales_29N * (pct_nulos_arr / 100.0)).astype(int)
    validos_29N = np.maximum(0, votos_totales_29N - nulos_29N)

    # Matriz de votos por opción válida (428 x 8)
    votos_matriz_29N = np.zeros((num_total_mesas, len(OPCIONES_VALIDAS)), dtype=np.float64)
    for m in range(num_total_mesas):
        if m in votos_reales_activas:
            df.at[m, "es_activa"] = 1
            v_real = votos_reales_activas[m]
            v_val_m = sum(v_real[p] for p in OPCIONES_VALIDAS)
            v_nul_m = v_real["Nulos"]
            v_tot_m = v_val_m + v_nul_m
            validos_29N[m] = v_val_m
            nulos_29N[m] = v_nul_m
            votos_totales_29N[m] = v_tot_m
            df.at[m, "participacion_29N"] = round(
                (v_tot_m / float(censos_arr[m])) * 100.0, 2
            ) if censos_arr[m] > 0 else 0.0
            df.at[m, "similitud_max_testigo"] = 100.0
            for j, p in enumerate(OPCIONES_VALIDAS):
                votos_matriz_29N[m, j] = float(v_real[p])
                P_projected_pct[m, j] = (float(v_real[p]) / float(v_val_m)) * 100.0
        else:
            votos_matriz_29N[m, :] = validos_29N[m] * (P_projected_pct[m, :] / 100.0)

    df["votos_totales_29N"] = votos_totales_29N
    df["nulos_29N"] = nulos_29N
    df["validos_29N"] = validos_29N

    for j, p in enumerate(OPCIONES_VALIDAS):
        df[f"{p}_29N"] = votos_matriz_29N[:, j]
        df[f"pct_29N_{p}"] = np.round(P_projected_pct[:, j], 2)
        df[f"swing_{p}"] = np.round(P_projected_pct[:, j] - df[f"pct_2023_{p}"], 2)

    # 3. Agregación Provincial Álava (428 mesas), Vitoria-Gasteiz (309 mesas) y Resto de Álava (119 mesas)
    total_censo_alava = int(df["censo_2023"].sum())
    total_votantes_29N = float(df["votos_totales_29N"].sum())
    total_nulos_29N = float(df["nulos_29N"].sum())
    total_validos_29N = float(np.sum(votos_matriz_29N))
    if total_validos_29N <= 0:
        total_validos_29N = 1.0

    votos_prov_29N = {
        p: float(df[f"{p}_29N"].sum()) for p in OPCIONES_VALIDAS
    }
    pct_prov_29N = {
        p: round((votos_prov_29N[p] / total_validos_29N) * 100.0, 2)
        for p in OPCIONES_VALIDAS
    }

    total_validos_2023 = float(df["validos_2023"].sum())
    votos_prov_2023 = {
        p: int(df[f"{p}_2023"].sum()) for p in OPCIONES_VALIDAS
    }
    pct_prov_2023 = {
        p: round((votos_prov_2023[p] / total_validos_2023) * 100.0, 2)
        for p in OPCIONES_VALIDAS
    }

    # Subtotales Vitoria-Gasteiz vs Resto de Álava
    df_vg = df[df["es_vitoria"] == 1]
    df_resto = df[df["es_vitoria"] == 0]
    val_vg_29N = max(1.0, float(sum(df_vg[f"{p}_29N"].sum() for p in OPCIONES_VALIDAS)))
    val_vg_2023 = max(1.0, float(df_vg["validos_2023"].sum()))
    val_resto_29N = max(1.0, float(sum(df_resto[f"{p}_29N"].sum() for p in OPCIONES_VALIDAS)))

    pct_vitoria_29N = {
        p: round((float(df_vg[f"{p}_29N"].sum()) / val_vg_29N) * 100.0, 2)
        for p in OPCIONES_VALIDAS
    }
    pct_vitoria_2023 = {
        p: round((float(df_vg[f"{p}_2023"].sum()) / val_vg_2023) * 100.0, 2)
        for p in OPCIONES_VALIDAS
    }
    pct_resto_29N = {
        p: round((float(df_resto[f"{p}_29N"].sum()) / val_resto_29N) * 100.0, 2)
        for p in OPCIONES_VALIDAS
    }

    # 4. Reparto de los 4 Escaños de Álava (Ley D'Hondt)
    dhondt_29N = compute_dhondt_alava(
        votos_partidos=votos_prov_29N,
        total_votos_validos=total_validos_29N,
        num_escanos=4,
        barrera_pct=3.0,
    )
    dhondt_2023 = compute_dhondt_alava(
        votos_partidos={p: float(votos_prov_2023[p]) for p in PARTIDOS_ESCANOS},
        total_votos_validos=total_validos_2023,
        num_escanos=4,
        barrera_pct=3.0,
    )

    # 5. Tablas Territoriales en Vivo:
    #    - Tab 1: 28 Barrios de Vitoria-Gasteiz
    #    - Tab 2: 7 Cuadrillas de Álava y 51 Municipios
    tabla_barrios_vg = build_territorial_table(df_vg, group_col="zona_barrio")
    tabla_cuadrillas = build_territorial_table(
        df, group_col="cuadrilla", custom_order=ORDEN_CUADRILLAS
    )
    tabla_municipios = build_territorial_table(
        df[df["cod_municipio"] != "999"], group_col="municipio", include_cuadrilla=True
    )

    participacion_prov_29N = round((total_votantes_29N / float(total_censo_alava)) * 100.0, 2)
    participacion_prov_2023 = round(
        (float(df["votos_totales_2023"].sum()) / float(total_censo_alava)) * 100.0, 2
    )

    # Avances horarios proyectados en Álava
    avance_14h_prov = round(
        float(np.sum(censos_arr * df["avance_14h_2023"].to_numpy()) / total_censo_alava)
        + (avances_delta_prov if "14:00h" in modo_horario else 0.0),
        2,
    )
    avance_18h_prov = round(
        float(np.sum(censos_arr * df["avance_18h_2023"].to_numpy()) / total_censo_alava)
        + (avances_delta_prov if "18:00h" in modo_horario else 0.0),
        2,
    )

    return {
        "df_extrapolado": df,
        "num_mesas_activas": N,
        "num_mesas_totales": num_total_mesas,
        "active_ids": active_ids,
        "gamma_N": round(gamma_N, 4),
        "tau": tau,
        "cobertura_sociologica_pct": round(cobertura_sociologica, 2),
        "margen_error_95_pp": margen_error_95,
        "censo_total_alava": total_censo_alava,
        "votos_totales_29N": int(round(total_votantes_29N)),
        "votos_validos_29N": int(round(total_validos_29N)),
        "votos_nulos_29N": int(round(total_nulos_29N)),
        "participacion_29N_pct": participacion_prov_29N,
        "participacion_2023_pct": participacion_prov_2023,
        "delta_participacion_pp": round(participacion_prov_29N - participacion_prov_2023, 2),
        "avance_14h_prov_pct": avance_14h_prov,
        "avance_18h_prov_pct": avance_18h_prov,
        "votos_prov_29N": {p: int(round(votos_prov_29N[p])) for p in OPCIONES_VALIDAS},
        "pct_prov_29N": pct_prov_29N,
        "votos_prov_2023": votos_prov_2023,
        "pct_prov_2023": pct_prov_2023,
        "swing_prov_pp": {
            p: round(pct_prov_29N[p] - pct_prov_2023[p], 2) for p in OPCIONES_VALIDAS
        },
        "pct_vitoria_29N": pct_vitoria_29N,
        "pct_vitoria_2023": pct_vitoria_2023,
        "pct_resto_alava_29N": pct_resto_29N,
        "dhondt_29N": dhondt_29N,
        "dhondt_2023": dhondt_2023,
        "semaforos_por_mesa": semaforos_por_mesa,
        "tabla_barrios_vitoria": tabla_barrios_vg,
        "tabla_cuadrillas_alava": tabla_cuadrillas,
        "tabla_municipios_alava": tabla_municipios,
    }


def build_territorial_table(
    df_sub: pd.DataFrame,
    group_col: str,
    custom_order: Optional[List[str]] = None,
    include_cuadrilla: bool = False,
) -> pd.DataFrame:
    """
    Construye la tabla agregada en vivo por ámbito territorial (28 Barrios de Vitoria,
    7 Cuadrillas de Álava o 51 Municipios de Álava) con ganador, participación y % por partido.
    """
    records = []
    grouped = df_sub.groupby(group_col, sort=False)

    for name, grp in grouped:
        censo_g = int(grp["censo_2023"].sum())
        mesas_tot = len(grp)
        mesas_act = int(grp["es_activa"].sum())
        vot_29n = float(grp["votos_totales_29N"].sum())
        val_29n = max(1.0, float(sum(grp[f"{p}_29N"].sum() for p in OPCIONES_VALIDAS)))
        val_23j = max(1.0, float(grp["validos_2023"].sum()))
        part_29n = round((vot_29n / float(censo_g)) * 100.0, 1) if censo_g > 0 else 0.0

        pcts_29n = {
            p: round((float(grp[f"{p}_29N"].sum()) / val_29n) * 100.0, 2)
            for p in PARTIDOS_ESCANOS
        }
        pcts_23j = {
            p: round((float(grp[f"{p}_2023"].sum()) / val_23j) * 100.0, 2)
            for p in PARTIDOS_ESCANOS
        }

        ganador_29n = max(PARTIDOS_ESCANOS, key=lambda p: pcts_29n[p])
        ganador_23j = max(PARTIDOS_ESCANOS, key=lambda p: pcts_23j[p])
        segundo_29n = sorted(PARTIDOS_ESCANOS, key=lambda p: pcts_29n[p], reverse=True)[1]
        ventaja_pp = round(pcts_29n[ganador_29n] - pcts_29n[segundo_29n], 2)

        rec: Dict[str, Any] = {
            "Territorio": name,
        }
        if include_cuadrilla:
            rec["Cuadrilla"] = str(grp["cuadrilla"].iloc[0])
        rec.update(
            {
                "Mesas": f"{mesas_act}/{mesas_tot}",
                "Censo": censo_g,
                "Part. 29N (%)": part_29n,
                "Ganador 29N": ganador_29n,
                "Ventaja (pp)": ventaja_pp,
                "Ganador 23J": ganador_23j,
                "PSE-EE (%)": pcts_29n["PSE-EE"],
                "EH Bildu (%)": pcts_29n["EH Bildu"],
                "PP (%)": pcts_29n["PP"],
                "EAJ-PNV (%)": pcts_29n["EAJ-PNV"],
                "Sumar/Izq (%)": pcts_29n["Sumar/Izq"],
                "Vox (%)": pcts_29n["Vox"],
                "Votos Válidos 29N": int(round(val_29n)),
            }
        )
        records.append(rec)

    res_df = pd.DataFrame(records)
    if custom_order is not None and not res_df.empty:
        order_map = {k: i for i, k in enumerate(custom_order)}
        res_df["_ord"] = res_df["Territorio"].map(lambda x: order_map.get(x, 999))
        res_df = res_df.sort_values("_ord").drop(columns=["_ord"]).reset_index(drop=True)
    elif not res_df.empty:
        res_df = res_df.sort_values("Censo", ascending=False).reset_index(drop=True)
    return res_df

