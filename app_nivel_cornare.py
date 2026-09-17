"""
App de Streamlit — Nivel de ríos/quebradas (CORNARE / MARCO)
--------------------------------------------------------------------
Personalizada para la estación 42 — Quebrada Santiago (Santo Domingo).

Cambios respecto a la app base:
  * Ficha de la estación (nombre, corriente, municipio, coordenadas,
    foto y enlace al geoportal) tomada del endpoint de metadatos.
  * Coordenadas reales: se usan las que entrega la API, no el punto
    por defecto (Pascual Bravo).
  * Métricas ampliadas: nivel actual, nivel máximo con su fecha,
    categoría de la estación (semáforo) e índice de calidad.
  * Pestañas con serie + media móvil, patrón horario y datos/calidad.

Para correrla:
    streamlit run app_nivel_cornare.py
"""

import requests
import pandas as pd
import numpy as np
import streamlit as st
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ------------------------------------------------------------------
# Estación por defecto: 42 — Quebrada Santiago (Santo Domingo)
# Los metadatos se refrescan desde la API; si la API falla se usan
# estos valores como respaldo.
# ------------------------------------------------------------------
ESTACION_POR_DEFECTO = {
    "codigo": "42",
    "label": "Santo Domingo, Quebrada Santiago (Red Agua - Cód. 42)",
    "corriente": "Quebrada Santiago",
    "municipio": "Santo Domingo",
    "region": 4,
    "red": "Agua",
    "ubicacion_campo": "PCH Santiago",
    "latitud": 6.542,
    "longitud": -75.1576,
    "categoria": "Seguro",
    "color_categoria": "#00833e",
    "nivel_actual": None,
    "foto": None,
}

API_BASE_URL = "https://marco.cornare.gov.co/api/v1/estaciones"
GEOPORTAL_URL = "https://marco.cornare.gov.co/geoportal"

LLAVE_FECHA = "fecha"
LLAVE_VALOR = "nivel"
LLAVES_ADICIONALES = ["muestra", "caudal", "calidad"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

st.set_page_config(page_title="Nivel — Quebrada Santiago (CORNARE)", page_icon="🌊", layout="wide")


# ------------------------------------------------------------------
# Funciones de consulta
# ------------------------------------------------------------------
def obtener_serie_nivel(codigo_estacion, desde, hasta, calidad=1, timeout=30):
    url = f"{API_BASE_URL}/{codigo_estacion}/nivel"
    params = {"desde": desde, "hasta": hasta, "calidad": calidad}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout, verify=False)
        if resp.status_code == 200:
            return resp.json(), None
        return None, f"HTTP {resp.status_code}"
    except requests.exceptions.RequestException as e:
        return None, f"Error de red: {e}"


def obtener_todas_las_paginas(datos_json, timeout=30):
    registros = list(datos_json.get("values", []))
    siguiente_url = datos_json.get("next")
    while siguiente_url:
        try:
            resp = requests.get(siguiente_url, timeout=timeout, verify=False)
        except requests.exceptions.RequestException:
            break
        if resp.status_code != 200:
            break
        pagina = resp.json()
        registros.extend(pagina.get("values", []))
        siguiente_url = pagina.get("next")
    return registros


def obtener_metadatos_estacion(codigo_estacion, timeout=30):
    """Metadatos reales de la estación (nombre, coords, foto, categoría)."""
    url = f"{API_BASE_URL}/{codigo_estacion}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, verify=False)
        if resp.status_code != 200:
            return None
        return resp.json()
    except requests.exceptions.RequestException:
        return None


def interpretar_metadatos(meta, codigo):
    """Mezcla metadatos reales (si existen) con los valores por defecto."""
    info = dict(ESTACION_POR_DEFECTO)
    if isinstance(meta, dict):
        info["codigo"] = str(meta.get("codigo", codigo))
        info["label"] = meta.get("label", info["label"])
        info["corriente"] = meta.get("corriente", info["corriente"])
        info["ubicacion_campo"] = meta.get("ubicacion_campo", info["ubicacion_campo"])
        info["red"] = meta.get("red", info["red"])

        try:
            info["latitud"] = float(meta.get("latitud", info["latitud"]))
            info["longitud"] = float(meta.get("longitud", info["longitud"]))
        except (TypeError, ValueError):
            pass

        sensor = meta.get("sensores", {}).get("nivel", {}) if isinstance(meta.get("sensores"), dict) else {}
        info["categoria"] = sensor.get("categoria", info["categoria"])
        info["color_categoria"] = sensor.get("color", info["color_categoria"])

        fotos = meta.get("fotos", [])
        if fotos:
            info["foto"] = fotos[0].get("foto")
    return info


def calcular_indice_calidad(df):
    """Índice simple (0-100): completitud de la serie (70%) + sin outliers (30%)."""
    if df.empty or len(df) < 2:
        return 0.0, 0, 0

    df_idx = df.set_index("fecha")
    frecuencia_tipica = df["fecha"].diff().dropna().mode()
    if len(frecuencia_tipica) == 0:
        return 0.0, 0, 0
    frecuencia_tipica = frecuencia_tipica[0]

    rango_completo = pd.date_range(start=df_idx.index.min(), end=df_idx.index.max(), freq=frecuencia_tipica)
    esperados = len(rango_completo)
    huecos = esperados - len(df_idx)
    completitud = max(0.0, 1 - (huecos / esperados)) if esperados > 0 else 0.0

    Q1, Q3 = df["nivel"].quantile(0.25), df["nivel"].quantile(0.75)
    IQR = Q3 - Q1
    lim_inf, lim_sup = Q1 - 1.5 * IQR, Q3 + 1.5 * IQR
    es_outlier = (df["nivel"] < lim_inf) | (df["nivel"] > lim_sup) | (df["nivel"] < 0)
    proporcion_outliers = es_outlier.mean()

    indice = (completitud * 0.7 + (1 - proporcion_outliers) * 0.3) * 100
    return round(indice, 1), int(huecos), int(es_outlier.sum())


# ------------------------------------------------------------------
# Sidebar — parámetros de la consulta (por defecto: estación 42)
# ------------------------------------------------------------------
st.sidebar.header("⚙️ Parámetros de tu consulta")
nombre_estudiante = st.sidebar.text_input("Nombre del estudiante", "Tu Nombre Aquí")
codigo_estacion = st.sidebar.text_input("Código de estación", "42")
fecha_desde = st.sidebar.date_input("Desde", pd.to_datetime("2026-08-30")).strftime("%Y-%m-%d")
fecha_hasta = st.sidebar.date_input("Hasta", pd.to_datetime("2026-09-02")).strftime("%Y-%m-%d")
calidad = st.sidebar.selectbox("Calidad", [1, 0], index=0, help="1 = solo datos validados")
consultar = st.sidebar.button("🔍 Consultar", type="primary")

# ------------------------------------------------------------------
# Encabezado
# ------------------------------------------------------------------
st.title("🌊 Nivel de ríos y quebradas — CORNARE")
st.caption(f"Estudiante: **{nombre_estudiante}** · Código de estación: **{codigo_estacion}**")

# ------------------------------------------------------------------
# Consulta y procesamiento
# ------------------------------------------------------------------
if consultar:
    with st.spinner("Consultando la API de CORNARE..."):
        meta = obtener_metadatos_estacion(codigo_estacion)
        info = interpretar_metadatos(meta, codigo_estacion)
        datos_crudos, error = obtener_serie_nivel(codigo_estacion, fecha_desde, fecha_hasta, calidad)

    # --- Ficha de la estación (siempre que haya metadatos o respaldo) ---
    col_ficha, col_foto = st.columns([2, 1])
    with col_ficha:
        st.subheader(f"📍 {info['label']}")
        st.markdown(
            f"- **Corriente:** {info['corriente']}  ·  **Red:** {info['red']}  ·  **Campo:** {info['ubicacion_campo']}  "
            f"·  **Región:** {info['region']}"
        )
        st.markdown(f"- **Coordenadas:** {info['latitud']:.4f}, {info['longitud']:.4f}")
        st.link_button("🌐 Ver en el geoportal de MARCO", f"{GEOPORTAL_URL}/{info['codigo']}")

    # Nivel actual y categoría (semáforo)
    nivel_actual = datos_crudos.get("current_level") if isinstance(datos_crudos, dict) else None
    if nivel_actual is None:
        nivel_actual = info.get("nivel_actual")
    if nivel_actual is not None:
        with col_foto:
            st.metric("Nivel actual (ahora)", f"{nivel_actual:.2f} cm")
            st.markdown(
                f"<span style='background:{info['color_categoria']}; color:white; padding:4px 12px; "
                f"border-radius:8px; font-weight:bold;'>Categoría: {info['categoria']}</span>",
                unsafe_allow_html=True,
            )

    if error:
        st.error(f"❌ {error}")
    elif not isinstance(datos_crudos, dict):
        st.error("❌ Respuesta inesperada de la API.")
    else:
        registros = obtener_todas_las_paginas(datos_crudos)

        if not registros:
            st.warning("No hay registros para esta estación y rango de fechas. Prueba otro código u otro rango.")
        else:
            df = pd.DataFrame(registros)
            df["fecha"] = pd.to_datetime(df[LLAVE_FECHA], errors="coerce")
            df["nivel"] = pd.to_numeric(df[LLAVE_VALOR], errors="coerce")
            for col in LLAVES_ADICIONALES:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=["fecha", "nivel"]).sort_values("fecha").reset_index(drop=True)

            indice_calidad, huecos, n_outliers = calcular_indice_calidad(df)
            pico = datos_crudos.get("max_level")
            fecha_pico = pd.to_datetime(datos_crudos.get("max_level_date"), errors="coerce")

            # --- Métricas principales ---
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Lecturas", len(df))
            c2.metric("Nivel promedio", f"{df['nivel'].mean():.2f} cm")
            c3.metric("Nivel máximo", f"{pico:.2f} cm" if pico is not None else f"{df['nivel'].max():.2f} cm")
            c4.metric("Fecha del máximo", fecha_pico.strftime("%d/%m %H:%M") if fecha_pico is not pd.NaT else "—")
            c5.metric("Índice de calidad", f"{indice_calidad} / 100")

            tab_serie, tab_hora, tab_datos = st.tabs([
                "📈 Serie de nivel", "🕐 Patrón horario", "📊 Datos y calidad",
            ])

            # --- Pestaña: serie + media móvil ---
            with tab_serie:
                st.subheader("Serie de nivel y media móvil")
                ventana = st.slider("Ventana de la media móvil (lecturas)", 6, 288, 60, step=6)
                serie_graf = df.set_index("fecha")["nivel"]
                df_graf = pd.DataFrame({"Nivel": serie_graf.values}, index=serie_graf.index)
                df_graf["Media móvil"] = df_graf["Nivel"].rolling(ventana).mean()
                st.line_chart(df_graf)
                st.caption(
                    "💡 La media móvil suaviza el ruido minuto a minuto y resalta la tendencia de la serie. "
                    f"El máximo del período fue **{pico:.2f} cm** el {fecha_pico.strftime('%d/%m/%Y a las %H:%M') if fecha_pico is not pd.NaT else '—'}."
                )
                if pico is not None and fecha_pico is not pd.NaT:
                    st.warning(f"⚠️ Posible evento de crecida: el nivel alcanzó {pico:.2f} cm (máximo de la consulta).")

            # --- Pestaña: patrón horario ---
            with tab_hora:
                st.subheader("Nivel promedio por hora del día")
                df["hora"] = df["fecha"].dt.hour
                resumen_hora = df.groupby("hora")["nivel"].mean()
                st.line_chart(resumen_hora)
                hora_pico = resumen_hora.idxmax()
                st.info(
                    f"💡 La hora con mayor nivel promedio es las **{hora_pico:02d}:00**. "
                    "Esto ayuda a ver si predomina un patrón de lluvias (p. ej. tardes convectivas en el Oriente antioqueño)."
                )

            # --- Pestaña: datos y calidad ---
            with tab_datos:
                st.subheader("Resumen estadístico")
                resumen = df["nivel"].agg(["count", "mean", "std", "min", "max"]).rename(
                    index={"count": "n", "mean": "media", "std": "desv. estándar", "min": "mínimo", "max": "máximo"}
                )
                st.dataframe(resumen.round(3).to_frame("nivel (cm)"), use_container_width=True)

                with st.expander("Detalle del índice de calidad"):
                    st.write(f"- Huecos de reporte detectados: **{huecos}**")
                    st.write(f"- Outliers (IQR + nivel negativo): **{n_outliers}** de {len(df)} lecturas")
                    st.write("El índice combina completitud de la serie (70%) y proporción de datos sin outliers (30%).")

                with st.expander("Ver datos crudos"):
                    orden = [c for c in ["fecha", "nivel", "muestra", "caudal", "calidad"] if c in df.columns]
                    st.dataframe(df[orden].head(1000), use_container_width=True)

                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button("⬇️ Descargar CSV", csv, file_name=f"nivel_estacion_{codigo_estacion}.csv", mime="text/csv")

                if info["foto"]:
                    st.image(info["foto"], caption="Estación 42 — Quebrada Santiago", width=420)
else:
    st.info("Ajusta los parámetros en el sidebar y presiona **Consultar**.")