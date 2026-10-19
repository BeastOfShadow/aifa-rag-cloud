"""UI locale per esplorare i due DB: Chroma (vector) + SQLite (strutturato).

Avvio:  cd rag && ../.venv/bin/streamlit run ui.py --server.fileWatcherType none
(il fileWatcher off evita il crash noto streamlit+torch)
"""
import sqlite3
import pandas as pd
import streamlit as st
import s5_query as s5
from config import DB_PATH, CHROMA_COLLECTION

st.set_page_config(page_title="Farmaci RAG — explorer", page_icon="💊", layout="wide")
st.title("💊 Farmaci RAG — explorer")


@st.cache_resource(show_spinner="Carico modelli (bge-m3 + reranker)...")
def _warm():
    s5._lazy()          # carica embedder + reranker + collection una volta
    return s5._coll.count()


chunk_count = _warm()
st.caption(f"Chroma: **{chunk_count:,}** chunk · collection `{CHROMA_COLLECTION}` · SQLite: `atc` `pa` `conf` `docs`")

tab_sem, tab_sql = st.tabs(["🔎 Ricerca semantica (Chroma)", "🗄️ SQL (SQLite)"])

# ---------- Ricerca semantica ----------
with tab_sem:
    q = st.text_input("Domanda / query", "posologia e controindicazioni dell'ibuprofene")
    c1, c2, c3 = st.columns(3)
    k = c1.slider("Top-k (dopo rerank)", 1, 20, 6)
    n = c2.slider("Candidati dense", 10, 60, 30)
    tipo = c3.selectbox("Tipo documento", ["tutti", "FI", "RCP"])

    if st.button("Cerca", type="primary") and q.strip():
        where = None if tipo == "tutti" else {"tipo": tipo}
        with st.spinner("dense search + cross-encoder rerank..."):
            hits = s5.retrieve(q, k=k, n=n, where=where)
        st.write(f"{len(hits)} risultati")
        for h in hits:
            m = h["meta"]
            st.markdown(
                f"**{m.get('denominazione', '?')}** · `{m['tipo']}` · "
                f"{m.get('section', '')}  \n"
                f"rerank **{h['score']}** · dense {h['dense']} · "
                f"ATC `{m.get('codice_atc', '')}` · ditta {m.get('ragione_sociale', '')}"
            )
            with st.expander("testo chunk"):
                st.text(h["text"])
            st.divider()

# ---------- SQL ----------
with tab_sql:
    st.caption("Tabelle: `atc` (7.209) · `pa` (337.550) · `conf` (159.708) · `docs` (manifest download/md)")
    examples = {
        "Confezioni (prime 50)": "SELECT * FROM conf LIMIT 50",
        "Farmaci per ditta": "SELECT RAGIONE_SOCIALE, COUNT(DISTINCT COD_FARMACO) n FROM conf GROUP BY RAGIONE_SOCIALE ORDER BY n DESC LIMIT 20",
        "Principi attivi piu' frequenti": "SELECT PRINCIPIO_ATTIVO, COUNT(*) n FROM pa WHERE PRINCIPIO_ATTIVO!='N.D.' GROUP BY PRINCIPIO_ATTIVO ORDER BY n DESC LIMIT 20",
        "Documenti mancanti (per motivo)": "SELECT http, COUNT(*) n FROM docs WHERE md_path IS NULL GROUP BY http",
        "Cerca farmaco per nome": "SELECT COD_FARMACO, DENOMINAZIONE, CODICE_ATC, RAGIONE_SOCIALE FROM conf WHERE DENOMINAZIONE LIKE '%BRUFEN%' LIMIT 50",
    }
    pick = st.selectbox("Esempi", list(examples))
    query = st.text_area("SQL", examples[pick], height=110)
    if st.button("Esegui SQL", type="primary"):
        con = sqlite3.connect(DB_PATH)
        try:
            df = pd.read_sql_query(query, con)
            st.dataframe(df, use_container_width=True, height=460)
            st.caption(f"{len(df)} righe")
        except Exception as e:
            st.error(str(e))
        finally:
            con.close()
