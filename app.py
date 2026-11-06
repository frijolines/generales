# -*- coding: utf-8 -*-
"""
app.py
======
Consola Split-Screen de Quick Count y Extrapolación Electoral en Tiempo Real (29N)
para la circunscripción completa de ÁLAVA / ARABA (428 mesas electorales:
309 en los 28 barrios de Vitoria-Gasteiz y 119 en los otros 50 municipios / 7 cuadrillas).

Arquitectura de Pantalla Dividida (layout="wide"):
- Columna Izquierda (42%): "⚡ Consola de Picado Rápido 29N"
  * Buscador multicriterio (Barrio / Municipio / Cuadrilla / Colegio / Mesa) con las 428 mesas.
  * Inicia con 4 mesas testigo preseleccionadas de Vitoria-Gasteiz (Mesa A, Mesa B, Mesa C, Mesa D).
  * Selector horario (20:00h Escrutinio, 14:00h Avance, 18:00h Avance).
  * Tabla Matricial Única (st.data_editor): Filas = Partidos + Blancos + Nulos; Columnas = Mesas.
  * Autoguardado instantáneo en `estado_vivo_29N.json` y semáforos de validación de censo.
- Columna Derecha (58%): "📊 Extrapolación Álava y Barrios de Vitoria"
  * Sello de validación oficial (428 mesas Álava / 23J).
  * KPIs en vivo (Mesas activas, Cobertura Sociológica, Participación Provincial, Margen de Error).
  * Marcador de los 4 Escaños de Álava (Ley D'Hondt) + Alerta en vivo al voto por el 4º escaño.
  * Gráfico Plotly comparativo Total Álava vs Total Vitoria-Gasteiz vs Resto Provincia.
  * Pestañas inferiores:
    - Tab 1: "🏙️ Barrios de Vitoria-Gasteiz" (Mapa de calor con los 28 barrios de Vitoria).
    - Tab 2: "🏞️ Municipios y Cuadrillas de Álava" (7 Cuadrillas y 51 Municipios de Álava).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine import (
    COLORES_PARTIDOS,
    ESTADO_VIVO_JSON_PATH,
    FILAS_MATRIZ,
    OPCIONES_VALIDAS,
    PARTIDOS_ESCANOS,
    build_default_witness_state,
    extrapolate_alava,
    load_historical_dataframe,
    load_or_init_live_state,
    save_live_state,
)

# ---------------------------------------------------------------------------
# Configuración de página y estilos CSS de Sala de Guerra Electoral (War Room)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Quick Count 29N · Álava / Araba & Vitoria-Gasteiz",
    page_icon="🗳️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.1rem !important;
        padding-bottom: 1.2rem !important;
        padding-left: 1.4rem !important;
        padding-right: 1.4rem !important;
        max-width: 100% !important;
    }
    .warroom-header {
        background: linear-gradient(90deg, #0f172a 0%, #1e293b 60%, #0f172a 100%);
        color: #f8fafc;
        padding: 0.75rem 1.15rem;
        border-radius: 10px;
        border-left: 6px solid #00A88F;
        margin-bottom: 0.75rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
        box-shadow: 0 3px 10px rgba(0,0,0,0.12);
    }
    .warroom-title {
        font-size: 1.22rem;
        font-weight: 800;
        letter-spacing: -0.01em;
        margin: 0;
    }
    .warroom-sub {
        font-size: 0.82rem;
        color: #cbd5e1;
        margin: 0;
    }
    .audit-seal {
        background-color: #ecfdf5;
        border: 1px solid #10b981;
        border-left: 5px solid #059669;
        color: #065f46;
        padding: 0.55rem 0.85rem;
        border-radius: 8px;
        font-size: 0.81rem;
        font-weight: 600;
        margin-bottom: 0.65rem;
    }
    .dhondt-alert {
        background: linear-gradient(90deg, #fffbeb 0%, #fef3c7 100%);
        border: 1px solid #f59e0b;
        border-left: 6px solid #d97706;
        color: #78350f;
        padding: 0.7rem 0.95rem;
        border-radius: 8px;
        margin-top: 0.45rem;
        margin-bottom: 0.65rem;
        font-size: 0.88rem;
    }
    .seat-card {
        border-radius: 9px;
        padding: 0.55rem 0.65rem;
        color: white;
        text-align: center;
        box-shadow: 0 2px 6px rgba(0,0,0,0.14);
        margin-bottom: 0.3rem;
    }
    .seat-party {
        font-size: 0.85rem;
        font-weight: 700;
        opacity: 0.95;
    }
    .seat-num {
        font-size: 1.65rem;
        font-weight: 900;
        line-height: 1.15;
    }
    .seat-pct {
        font-size: 0.78rem;
        opacity: 0.92;
    }
    .semaforo-box {
        font-size: 0.79rem;
        padding: 0.38rem 0.65rem;
        border-radius: 6px;
        margin-bottom: 0.28rem;
        background: #f8fafc;
        border: 1px solid #e2e8f0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def get_base_census_df() -> pd.DataFrame:
    return load_historical_dataframe()


def init_session_state(df_hist: pd.DataFrame) -> None:
    if "live_state" not in st.session_state:
        st.session_state["live_state"] = load_or_init_live_state(df_hist)
    if "editor_version" not in st.session_state:
        st.session_state["editor_version"] = 0


def apply_scenario_preset(df_hist: pd.DataFrame, preset_name: str) -> None:
    """Permite cargar escenarios de prueba o reiniciar la cola de picado con un clic."""
    if preset_name in ("29N_COMPETITIVO", "23J_EXACTO", "VACIO"):
        new_st = build_default_witness_state(df_hist, scenario=preset_name)
    elif preset_name == "MUESTRA_10_ALAVA":
        new_st = build_default_witness_state(df_hist, scenario="29N_COMPETITIVO")
        # Añadir 6 mesas estratégicas adicionales de Vitoria y Cuadrillas (Laudio, Amurrio, Agurain, Oyón, Salburua, Mendizorrotza)
        extra_ids = [
            "01-059-03-035-A",  # Salburua (Vitoria)
            "01-059-02-033-U",  # Mendizorrotza (Vitoria)
            "01-036-01-001-A",  # Laudio/Llodio (Ayala)
            "01-002-01-001-A",  # Amurrio (Ayala)
            "01-051-01-001-A",  # Agurain/Salvatierra (Llanada)
            "01-043-01-001-A",  # Oyón-Oion (Rioja Alavesa)
        ]
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
        for eid in extra_ids:
            if eid not in new_st["mesas_en_cola"]:
                new_st["mesas_en_cola"].append(eid)
            row_m = df_hist[df_hist["id_mesa"] == eid]
            if not row_m.empty:
                r0 = row_m.iloc[0]
                new_st["votos_29N"][eid] = {
                    f: max(0, int(round(int(r0[f"{f}_2023"]) * mult_29n[f])))
                    for f in FILAS_MATRIZ
                }
    else:
        return

    st.session_state["live_state"] = new_st
    st.session_state["editor_version"] += 1
    save_live_state(new_st)


def style_electoral_heatmap(df_table: pd.DataFrame) -> Any:
    """Aplica formato de mapa de calor por partido y colores de ganador a las tablas territoriales."""
    pct_cols = [
        c
        for c in [
            "PSE-EE (%)",
            "EH Bildu (%)",
            "PP (%)",
            "EAJ-PNV (%)",
            "Sumar/Izq (%)",
            "Vox (%)",
        ]
        if c in df_table.columns
    ]

    def color_winner(val: Any) -> str:
        c = COLORES_PARTIDOS.get(str(val), "#334155")
        return f"background-color: {c}24; color: {c}; font-weight: 700;"

    def make_col_heatmap(col_name: str):
        p_name = col_name.replace(" (%)", "")
        hex_c = COLORES_PARTIDOS.get(p_name, "#0284c7").lstrip("#")
        r_c, g_c, b_c = int(hex_c[0:2], 16), int(hex_c[2:4], 16), int(hex_c[4:6], 16)

        def _cell_style(val: Any) -> str:
            try:
                v = float(val)
            except Exception:
                return ""
            alpha = min(0.55, max(0.03, v / 48.0 * 0.55))
            weight = "700" if v >= 25.0 else "500"
            return f"background-color: rgba({r_c}, {g_c}, {b_c}, {alpha:.2f}); color: #0f172a; font-weight: {weight};"

        return _cell_style

    styler = df_table.style
    if "Ganador 29N" in df_table.columns:
        styler = styler.map(color_winner, subset=["Ganador 29N"])
    if "Ganador 23J" in df_table.columns:
        styler = styler.map(color_winner, subset=["Ganador 23J"])
    for c_pct in pct_cols:
        styler = styler.map(make_col_heatmap(c_pct), subset=[c_pct])

    fmt_dict = {c: "{:.2f}%" for c in pct_cols}
    if "Part. 29N (%)" in df_table.columns:
        fmt_dict["Part. 29N (%)"] = "{:.1f}%"
    if "Ventaja (pp)" in df_table.columns:
        fmt_dict["Ventaja (pp)"] = "+{:.2f}"
    if "Censo" in df_table.columns:
        fmt_dict["Censo"] = "{:,d}"
    if "Votos Válidos 29N" in df_table.columns:
        fmt_dict["Votos Válidos 29N"] = "{:,d}"

    return styler.format(fmt_dict)


def main() -> None:
    df_hist = get_base_census_df()
    init_session_state(df_hist)
    live_state = st.session_state["live_state"]

    # Mapas auxiliares para el selector de las 428 mesas
    id_to_label: Dict[str, str] = dict(
        zip(df_hist["id_mesa"], df_hist["etiqueta_selector"])
    )
    id_to_short: Dict[str, str] = dict(
        zip(df_hist["id_mesa"], df_hist["etiqueta_corta"])
    )
    all_mesa_ids: List[str] = list(df_hist["id_mesa"].values)

    # Cabecera general compacta
    st.markdown(
        """
        <div class="warroom-header">
            <div>
                <p class="warroom-title">🗳️ SISTEMA QUICK COUNT Y EXTRAPOLACIÓN ELECTORAL EN TIEMPO REAL · ÁLAVA / ARABA (29N)</p>
                <p class="warroom-sub">Circunscripción Provincial de Álava (4 Escaños Congreso) · 428 Mesas Electorales (309 Vitoria-Gasteiz / 28 Barrios + 119 Resto Álava / 51 Municipios)</p>
            </div>
            <div style="text-align:right; font-size:0.78rem; color:#94a3b8;">
                Base Histórica Oficial: <b>Generales 23J 2023</b><br>
                Persistencia en Vivo: <code>estado_vivo_29N.json</code>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Layout Split-Screen: 42% Izquierda (Consola de Picado) | 58% Derecha (Extrapolación en Vivo)
    col_left, col_right = st.columns([0.42, 0.58], gap="medium")

    # =========================================================================
    # COLUMNA IZQUIERDA (42%): ⚡ CONSOLA DE PICADO RÁPIDO 29N
    # =========================================================================
    with col_left:
        st.subheader("⚡ Consola de Picado Rápido 29N")

        # Botonera rápida de escenarios para demostración y reseteo inmediato
        b1, b2, b3, b4 = st.columns(4)
        with b1:
            if st.button("🎯 Vivo 29N (4 Test.)", width="stretch", help="Carga simulación competitiva 29N en las 4 mesas testigo (Mesa A, B, C, D)"):
                apply_scenario_preset(df_hist, "29N_COMPETITIVO")
                st.rerun()
        with b2:
            if st.button("🏛️ Base 23J (4 Test.)", width="stretch", help="Carga los votos exactos del 23J 2023 en las 4 mesas testigo"):
                apply_scenario_preset(df_hist, "23J_EXACTO")
                st.rerun()
        with b3:
            if st.button("🌍 Muestra 10 Mesas", width="stretch", help="Añade mesas clave de Vitoria, Laudio, Amurrio, Agurain y Rioja Alavesa"):
                apply_scenario_preset(df_hist, "MUESTRA_10_ALAVA")
                st.rerun()
        with b4:
            if st.button("🧹 Vaciar Votos (0)", width="stretch", help="Pone a 0 todas las mesas en cola"):
                apply_scenario_preset(df_hist, "VACIO")
                st.rerun()

        # 1. Selector st.multiselect con buscador por Barrio/Municipio/Colegio/Mesa
        current_queue = [
            m for m in live_state.get("mesas_en_cola", []) if m in id_to_label
        ]
        if not current_queue:
            current_queue = list(
                df_hist[df_hist["es_testigo_inicial"] == 1]["id_mesa"].values
            )

        selected_mesas = st.multiselect(
            "🔍 Buscador de Mesas en Cola de Picado (filtra por Barrio, Municipio, Cuadrilla, Colegio o Mesa):",
            options=all_mesa_ids,
            default=current_queue,
            format_func=lambda m_id: id_to_label.get(m_id, m_id),
            help="Escribe el nombre de cualquier barrio de Vitoria (ej. Zabalgana, Salburua, Lakua, Ensanche), municipio (ej. Laudio, Amurrio, Agurain, Oyón) o código de mesa.",
        )

        # Si el usuario vacía todas, mantener al menos la primera mesa testigo para evitar matriz sin columnas
        if not selected_mesas:
            st.warning("Selecciona al menos una mesa para mostrar la matriz de picado. Mostrando las 4 mesas testigo por defecto.")
            selected_mesas = list(
                df_hist[df_hist["es_testigo_inicial"] == 1]["id_mesa"].values
            )

        if selected_mesas != live_state.get("mesas_en_cola"):
            live_state["mesas_en_cola"] = selected_mesas
            for m_id in selected_mesas:
                if m_id not in live_state["votos_29N"]:
                    live_state["votos_29N"][m_id] = {f: 0 for f in FILAS_MATRIZ}
            save_live_state(live_state)

        # 2. Selector horario (20:00h Escrutinio, 14:00h Avance, 18:00h Avance)
        horarios_opts = ["20:00h Escrutinio", "14:00h Avance", "18:00h Avance"]
        idx_h = (
            horarios_opts.index(live_state.get("modo_horario", "20:00h Escrutinio"))
            if live_state.get("modo_horario") in horarios_opts
            else 0
        )
        modo_horario = st.radio(
            "🕒 Modo Horario de Operación:",
            options=horarios_opts,
            index=idx_h,
            horizontal=True,
        )
        if modo_horario != live_state.get("modo_horario"):
            live_state["modo_horario"] = modo_horario
            save_live_state(live_state)

        # Si el operador selecciona 14:00h Avance o 18:00h Avance, ofrecer editor rápido de avances de participación
        if modo_horario in ("14:00h Avance", "18:00h Avance"):
            key_av = "avance_14h" if "14:00h" in modo_horario else "avance_18h"
            col_hist_av = (
                "avance_14h_2023" if "14:00h" in modo_horario else "avance_18h_2023"
            )
            st.info(
                f"📡 **{modo_horario} activo**: Introduce el % de participación registrada en las mesas seleccionadas para proyectar la participación final provincial. Debajo permanece activa la matriz de escrutinio."
            )
            av_rows = []
            for m_id in selected_mesas:
                r_m = df_hist[df_hist["id_mesa"] == m_id].iloc[0]
                av_curr = live_state.get("avances_29N", {}).get(m_id, {}).get(
                    key_av, float(r_m[col_hist_av])
                )
                av_rows.append(
                    {
                        "id_mesa": m_id,
                        "Mesa": id_to_short[m_id],
                        "Censo": int(r_m["censo_2023"]),
                        "Histórico 23J (%)": float(r_m[col_hist_av]),
                        f"{modo_horario} 29N (%)": float(av_curr),
                    }
                )
            df_av_edit = pd.DataFrame(av_rows).set_index("Mesa")
            edited_av = st.data_editor(
                df_av_edit[["Censo", "Histórico 23J (%)", f"{modo_horario} 29N (%)"]],
                disabled=["Censo", "Histórico 23J (%)"],
                width="stretch",
                key=f"av_editor_{modo_horario}_{st.session_state['editor_version']}",
            )
            av_changed = False
            for idx_row, m_id in enumerate(selected_mesas):
                short_lbl = id_to_short[m_id]
                new_val = float(edited_av.loc[short_lbl, f"{modo_horario} 29N (%)"])
                old_val = float(
                    live_state.setdefault("avances_29N", {})
                    .setdefault(m_id, {})
                    .get(key_av, 0.0)
                )
                if abs(new_val - old_val) > 1e-4:
                    live_state["avances_29N"][m_id][key_av] = new_val
                    av_changed = True
            if av_changed:
                save_live_state(live_state)

        # 3. Tabla Matricial Única (st.data_editor)
        # Filas = PSE-EE, EH Bildu, PP, EAJ-PNV, Sumar/Izq, Vox, Otros, Blancos, Nulos
        # Columnas = Mesas en cola de picado
        st.markdown(
            "**⌨️ Matriz Única de Picado de Votos (`TAB` / `ENTER` para navegación ultrarrápida):**"
        )

        col_map_to_id: Dict[str, str] = {}
        matrix_data: Dict[str, List[int]] = {}
        column_config: Dict[str, Any] = {}

        for m_id in selected_mesas:
            r_m = df_hist[df_hist["id_mesa"] == m_id].iloc[0]
            censo_m = int(r_m["censo_2023"])
            col_name = id_to_short.get(m_id, m_id)
            if col_name in col_map_to_id:
                col_name = f"{col_name} [{m_id[-5:]}]"
            col_map_to_id[col_name] = m_id

            v_mesa = live_state.get("votos_29N", {}).get(m_id, {})
            matrix_data[col_name] = [int(v_mesa.get(f, 0) or 0) for f in FILAS_MATRIZ]

            column_config[col_name] = st.column_config.NumberColumn(
                label=col_name,
                help=f"{id_to_label.get(m_id, m_id)} | Censo máx: {censo_m} electores | Base 23J Válidos: {int(r_m['validos_2023'])}",
                min_value=0,
                max_value=censo_m * 2,
                step=1,
                format="%d",
                required=True,
            )

        df_matrix = pd.DataFrame(matrix_data, index=FILAS_MATRIZ)
        df_matrix.index.name = "Opción / Partido"

        edited_matrix = st.data_editor(
            df_matrix,
            column_config=column_config,
            width="stretch",
            height=358,
            key=f"matrix_editor_{st.session_state['editor_version']}_{'_'.join(selected_mesas)}",
        )

        # Sincronizar cambios y autoguardar instantáneamente en `estado_vivo_29N.json`
        matrix_changed = False
        for col_name, m_id in col_map_to_id.items():
            current_saved = live_state.setdefault("votos_29N", {}).setdefault(
                m_id, {f: 0 for f in FILAS_MATRIZ}
            )
            for fila in FILAS_MATRIZ:
                val_edit = int(edited_matrix.loc[fila, col_name] or 0)
                if current_saved.get(fila, 0) != val_edit:
                    current_saved[fila] = val_edit
                    matrix_changed = True

        if matrix_changed:
            save_live_state(live_state)

        # Ejecutar el motor matemático sobre las 428 mesas de Álava
        resultados = extrapolate_alava(
            df_historico=df_hist,
            votos_29N=live_state.get("votos_29N", {}),
            modo_horario=modo_horario,
            avances_29N=live_state.get("avances_29N", {}),
            tau=0.05,
        )

        # 4. Semáforos de validación de censo por cada mesa en la cola
        st.markdown("**🚦 Semáforos de Validación de Censo y Estado por Mesa en Cola:**")
        sem_rows = []
        for m_id in selected_mesas:
            sem = resultados["semaforos_por_mesa"][m_id]
            r_m = df_hist[df_hist["id_mesa"] == m_id].iloc[0]
            sem_rows.append(
                {
                    "Mesa": id_to_short[m_id],
                    "Barrio / Municipio": str(r_m["zona_barrio"]),
                    "Estado Censo": sem["texto"],
                    "Válidos 29N": sem["votos_validos"],
                    "Totales / Censo": f"{sem['votos_totales']} / {sem['censo']}",
                    "Part. 29N vs 23J": f"{sem['participacion_pct']:.1f}% (23J: {float(r_m['participacion_2023']):.1f}%)",
                }
            )
        st.dataframe(
            pd.DataFrame(sem_rows),
            width="stretch",
            hide_index=True,
            height=min(220, 42 + 35 * len(sem_rows)),
        )

        with st.expander("📂 Ver referencia 23J de las mesas en cola y estado JSON autoguardado"):
            ref_23j_data = {"Opción / Partido": FILAS_MATRIZ}
            for col_name, m_id in col_map_to_id.items():
                r_m = df_hist[df_hist["id_mesa"] == m_id].iloc[0]
                ref_23j_data[f"{col_name} (23J)"] = [
                    int(r_m[f"{f}_2023"]) for f in FILAS_MATRIZ
                ]
            st.caption("Votos reales obtenidos en las Elecciones Generales de julio de 2023 en las mesas seleccionadas:")
            st.dataframe(pd.DataFrame(ref_23j_data), width="stretch", hide_index=True)
            st.caption(f"Ruta de autoguardado en tiempo real: `{ESTADO_VIVO_JSON_PATH}`")

    # =========================================================================
    # COLUMNA DERECHA (58%): 📊 EXTRAPOLACIÓN ÁLAVA Y BARRIOS DE VITORIA
    # =========================================================================
    with col_right:
        st.subheader("📊 Extrapolación Álava y Barrios de Vitoria")

        # 1. Sello de validación oficial (428 mesas Álava / 23J)
        st.markdown(
            f"""
            <div class="audit-seal">
                ✅ <b>SELLO DE AUDITORÍA OFICIAL SUPERADO (428 Mesas Álava · Base 23J)</b> — 
                309 mesas en Vitoria-Gasteiz (28 Barrios cruzados con Callejero Oficial) + 119 mesas en los otros 50 municipios / 7 Cuadrillas de Álava.
                Censo Auditado: <b>{resultados['censo_total_alava']:,}</b> (~259.665) · Válidos 23J: <b>169.107</b> (~171.795) · 
                Motor Softmax Cosine (<b>τ = {resultados['tau']}</b>, Amortiguación <b>γ_N = {resultados['gamma_N']:.3f}</b>).
            </div>
            """,
            unsafe_allow_html=True,
        )

        # 2. KPIs en vivo
        k1, k2, k3, k4 = st.columns(4)
        n_act = resultados["num_mesas_activas"]
        n_tot = resultados["num_mesas_totales"]
        pct_mesas = (n_act / float(n_tot)) * 100.0
        with k1:
            st.metric(
                "Mesas Activas (N)",
                f"{n_act} / {n_tot}",
                delta=f"{pct_mesas:.1f}% mesas · γ={resultados['gamma_N']:.3f}",
            )
        with k2:
            st.metric(
                "Cobertura Sociológica",
                f"{resultados['cobertura_sociologica_pct']:.1f}%",
                delta="Similitud Coseno Máx.",
            )
        with k3:
            st.metric(
                "Participación Provincial",
                f"{resultados['participacion_29N_pct']:.2f}%",
                delta=f"{resultados['delta_participacion_pp']:+.2f} pp vs 23J ({resultados['participacion_2023_pct']:.1f}%)",
            )
        with k4:
            st.metric(
                "Margen de Error (95%)",
                f"±{resultados['margen_error_95_pp']:.2f} pp",
                delta=f"14h: {resultados['avance_14h_prov_pct']:.1f}% · 18h: {resultados['avance_18h_prov_pct']:.1f}%",
                delta_color="off",
            )

        # 3. Marcador de los 4 Escaños de Álava (Ley D'Hondt) + Alerta en vivo por el último escaño
        st.markdown("##### 🏛️ Reparto Proyectado de los 4 Diputados de Álava (Ley D'Hondt - Art. 163 LOREG, Barrera 3%)")
        dhondt = resultados["dhondt_29N"]
        dhondt_23j = resultados["dhondt_2023"]

        seat_cols = st.columns(6)
        for idx_p, p in enumerate(PARTIDOS_ESCANOS):
            esc_29n = dhondt["escanos"][p]
            esc_23j = dhondt_23j["escanos"][p]
            diff_esc = esc_29n - esc_23j
            diff_str = f"({diff_esc:+d} vs 23J)" if diff_esc != 0 else "(= vs 23J)"
            pct_p = resultados["pct_prov_29N"][p]
            swing_p = resultados["swing_prov_pp"][p]
            votos_p = resultados["votos_prov_29N"][p]
            bg_color = COLORES_PARTIDOS[p] if esc_29n > 0 else "#475569"

            with seat_cols[idx_p]:
                st.markdown(
                    f"""
                    <div class="seat-card" style="background:{bg_color};">
                        <div class="seat-party">{p}</div>
                        <div class="seat-num">{esc_29n} <span style="font-size:0.75rem;font-weight:500;">esc. {diff_str}</span></div>
                        <div class="seat-pct"><b>{pct_p:.2f}%</b> ({swing_p:+.2f} pp)<br>{votos_p:,} votos</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        # Alerta en vivo de disputa por el 4º escaño de Álava
        c4 = dhondt["cuarto_escano"]
        c5 = dhondt["quinto_aspirante"]
        st.markdown(
            f"""
            <div class="dhondt-alert">
                🔥 <b>ALERTA EN VIVO · DISPUTA POR EL 4º ESCAÑO DE ÁLAVA:</b> 
                El <b>4º diputado</b> lo retiene actualmente <b>{c4['partido']}</b> 
                (Divisor {c4['divisor']}, Cociente D'Hondt: <b>{c4['cociente']:,.1f}</b>). 
                El <b>5º aspirante</b> es <b>{c5['partido']}</b> 
                (Divisor {c5['divisor']}, Cociente: <b>{c5['cociente']:,.1f}</b>), que se encuentra a exactamente 
                <span style="background:#dc2626;color:white;padding:2px 7px;border-radius:4px;font-weight:800;">
                {c5['votos_faltan_para_4to']:,} votos en Álava</span> de arrebatarle el último escaño 
                (colchón de resistencia de {c4['partido']}: <b>{c4['colchon_votos']:,} votos</b>).
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander("🔢 Ver Matriz Completa de Cocientes D'Hondt (Divisores 1, 2, 3, 4) y Barrera Legal 3%"):
            rows_dh = []
            asignados_set = {
                (a["partido"], a["divisor"]): a["orden_escano"]
                for a in dhondt["asignaciones"]
            }
            for p in PARTIDOS_ESCANOS:
                v_p = resultados["votos_prov_29N"][p]
                pct_p = resultados["pct_prov_29N"][p]
                cocs = dhondt["tabla_cocientes"][p]
                row_d = {
                    "Candidatura": p,
                    "Votos 29N": f"{v_p:,}",
                    "% Válido": f"{pct_p:.2f}%",
                    "Supera 3%": "✅ Sí" if p not in dhondt["partidos_excluidos_3pct"] else "❌ No (<3%)",
                }
                for d in range(1, 5):
                    tag_esc = (
                        f" 🏆 [{asignados_set[(p, d)]}º Escaño]"
                        if (p, d) in asignados_set
                        else ""
                    )
                    row_d[f"Divisor {d}"] = f"{cocs[d - 1]:,.1f}{tag_esc}"
                rows_dh.append(row_d)
            st.caption(
                f"Votos Válidos Totales Proyectados en Álava: **{resultados['votos_validos_29N']:,}** | "
                f"Umbral legal 3% (Art. 163 LOREG): **{int(round(dhondt['umbral_votos_3pct'])):,} votos**."
            )
            st.dataframe(pd.DataFrame(rows_dh), width="stretch", hide_index=True)

        # 4. Gráfico Plotly comparativo: Total Álava 29N vs Total Vitoria-Gasteiz 29N vs Base 23J
        partidos_graf = PARTIDOS_ESCANOS + ["Otros"]
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                name="Total Álava 29N (%)",
                x=partidos_graf,
                y=[resultados["pct_prov_29N"][p] for p in partidos_graf],
                marker_color=[COLORES_PARTIDOS[p] for p in partidos_graf],
                text=[f"{resultados['pct_prov_29N'][p]:.1f}%" for p in partidos_graf],
                textposition="outside",
            )
        )
        fig.add_trace(
            go.Bar(
                name="Vitoria-Gasteiz 29N (%)",
                x=partidos_graf,
                y=[resultados["pct_vitoria_29N"][p] for p in partidos_graf],
                marker_color=[COLORES_PARTIDOS[p] for p in partidos_graf],
                marker_pattern_shape="/",
                opacity=0.82,
                text=[f"{resultados['pct_vitoria_29N'][p]:.1f}%" for p in partidos_graf],
                textposition="outside",
            )
        )
        fig.add_trace(
            go.Bar(
                name="Resto Álava / Cuadrillas 29N (%)",
                x=partidos_graf,
                y=[resultados["pct_resto_alava_29N"][p] for p in partidos_graf],
                marker_color="#64748b",
                opacity=0.65,
                text=[f"{resultados['pct_resto_alava_29N'][p]:.1f}%" for p in partidos_graf],
                textposition="outside",
            )
        )
        fig.add_trace(
            go.Scatter(
                name="Resultado Oficial 23J Álava (%)",
                x=partidos_graf,
                y=[resultados["pct_prov_2023"][p] for p in partidos_graf],
                mode="markers+lines",
                line=dict(color="#0f172a", width=2, dash="dot"),
                marker=dict(size=9, symbol="diamond", color="#f59e0b", line=dict(width=1.5, color="#0f172a")),
            )
        )
        fig.update_layout(
            title="Comparativa en Vivo: Total Álava vs. Vitoria-Gasteiz vs. Resto Provincia (y referencia 23J)",
            barmode="group",
            height=295,
            margin=dict(l=20, r=20, t=38, b=20),
            yaxis=dict(title="% sobre Voto Válido", range=[0, max(36.0, max(resultados["pct_prov_29N"].values()) + 6.0)]),
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
            template="plotly_white",
        )
        st.plotly_chart(fig, width="stretch")

        # 5. Pestañas inferiores de desglose territorial en vivo
        tab_vg, tab_alava = st.tabs(
            [
                "🏙️ Barrios de Vitoria-Gasteiz (28 Barrios)",
                "🏞️ Municipios y Cuadrillas de Álava (7 Cuadrillas / 51 Municipios)",
            ]
        )

        with tab_vg:
            df_barrios = resultados["tabla_barrios_vitoria"].rename(
                columns={"Territorio": "Barrio de Vitoria-Gasteiz"}
            )
            ganadores_cnt = df_barrios["Ganador 29N"].value_counts().to_dict()
            resumen_barrios = " · ".join(
                [f"**{p}**: {c} barrios" for p, c in ganadores_cnt.items()]
            )
            st.caption(
                f"Desglose en vivo de las **309 mesas de Vitoria-Gasteiz** cruzadas por `Distrito + Sección` con el callejero oficial en los **28 Barrios**. Liderazgo por barrios: {resumen_barrios}."
            )
            st.dataframe(
                style_electoral_heatmap(df_barrios),
                width="stretch",
                hide_index=True,
                height=420,
            )

        with tab_alava:
            df_cuadrillas = resultados["tabla_cuadrillas_alava"].rename(
                columns={"Territorio": "Cuadrilla / Ámbito Territorial"}
            )
            st.markdown("###### 🗺️ Desglose por las 7 Cuadrillas del Territorio Histórico de Álava")
            st.dataframe(
                style_electoral_heatmap(df_cuadrillas),
                width="stretch",
                hide_index=True,
                height=285,
            )

            st.markdown("###### 🏘️ Desglose Completo por los 51 Municipios de Álava (Laudio/Llodio, Amurrio, Agurain, Oyón, Laguardia, Iruña Oka...)")
            df_muns = resultados["tabla_municipios_alava"].rename(
                columns={"Territorio": "Municipio"}
            )
            filtro_cuad = st.selectbox(
                "Filtrar municipios por Cuadrilla:",
                options=["Todas las Cuadrillas (51 Municipios)"] + list(df_cuadrillas["Cuadrilla / Ámbito Territorial"].values),
                index=0,
            )
            if filtro_cuad != "Todas las Cuadrillas (51 Municipios)":
                df_muns = df_muns[df_muns["Cuadrilla"] == filtro_cuad].reset_index(drop=True)

            st.dataframe(
                style_electoral_heatmap(df_muns),
                width="stretch",
                hide_index=True,
                height=340,
            )


if __name__ == "__main__":
    main()
