import streamlit as st 
import pandas as pd
import requests
import time
import plotly.express as px
from datetime import datetime, timedelta
from io import BytesIO

# Importação do utils
from utils import check_password, logout_button

# Configurações
st.set_page_config(page_title="Relatório Gerencial Intercom", page_icon="📊", layout="wide")

# Bloqueio de senha
usuario = check_password()

if not usuario:
    st.stop()

if usuario == "analista":
    st.error("⛔ Acesso Negado: Área restrita à gestão.")
    st.info("Utilize o menu lateral para acessar o **Painel do Analista**.")
    st.stop()

WORKSPACE_ID = "xwvpdtlu"

# Autenticação Intercom
try:
    INTERCOM_ACCESS_TOKEN = st.secrets["INTERCOM_TOKEN"]
except:
    INTERCOM_ACCESS_TOKEN = st.sidebar.text_input("Intercom Token", type="password", key="token_gerencial")

if not INTERCOM_ACCESS_TOKEN:
    st.warning("⚠️ Configure o Token.")
    st.stop()

HEADERS = {"Authorization": f"Bearer {INTERCOM_ACCESS_TOKEN}", "Accept": "application/json"}

# Funções

def format_sla_string(seconds):
    if not seconds or pd.isna(seconds) or seconds == 0: return "-"
    seconds = int(seconds)
    days = seconds // 86400
    rem = seconds % 86400
    hours = rem // 3600
    rem %= 3600
    minutes = rem // 60
    secs = rem % 60
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    if minutes > 0: parts.append(f"{minutes}m")
    if days == 0 and hours == 0: parts.append(f"{secs}s")
    return " ".join(parts) if parts else "< 1s"

@st.cache_data(ttl=3600)
def get_attribute_definitions():
    url = "https://api.intercom.io/data_attributes"
    params = {"model": "conversation"}
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        return {item['name']: item['label'] for item in r.json().get('data', [])}
    except:
        return {}

@st.cache_data(ttl=3600)
def get_all_admins():
    url = "https://api.intercom.io/admins"
    try:
        r = requests.get(url, headers=HEADERS)
        return {str(a['id']): a['name'] for a in r.json().get('admins', [])}
    except:
        return {}

@st.cache_data(ttl=300, show_spinner=False)
def fetch_conversations(start_date, end_date, team_ids=None):
    url = "https://api.intercom.io/conversations/search"
    ts_start = int(datetime.combine(start_date, datetime.min.time()).timestamp())
    ts_end = int(datetime.combine(end_date, datetime.max.time()).timestamp())
    
    query_rules = [
        {"field": "created_at", "operator": ">", "value": ts_start},
        {"field": "created_at", "operator": "<", "value": ts_end}
    ]
    if team_ids:
        query_rules.append({"field": "team_assignee_id", "operator": "IN", "value": team_ids})

    payload = {"query": {"operator": "AND", "value": query_rules}, "pagination": {"per_page": 150}}
    
    conversas = []
    has_more = True
    status_text = st.empty()
    
    while has_more:
        try:
            resp = requests.post(url, headers=HEADERS, json=payload)
            data = resp.json()
            batch = data.get('conversations', [])
            conversas.extend(batch)
            status_text.caption(f"📥 Baixando... {len(conversas)} conversas.")
            
            if data.get('pages', {}).get('next'):
                payload['pagination']['starting_after'] = data['pages']['next']['starting_after']
                time.sleep(0.1)
            else:
                has_more = False
        except Exception as e:
            st.error(f"Erro: {e}")
            break
    status_text.empty()
    return conversas

def process_data(conversas, mapping, admin_map):
    rows = []
    for c in conversas:
        link = f"https://app.intercom.com/a/inbox/{WORKSPACE_ID}/inbox/conversation/{c['id']}"
        admin_id = c.get('admin_assignee_id')
        assignee_name = admin_map.get(str(admin_id), f"ID {admin_id}") if admin_id else "Não atribuído"

        # Pega o ID da Equipe
        team_id = c.get('team_assignee_id')
        nome_equipe = str(team_id) if team_id else "Sem equipe"
        
        # Captura e traduz o estado nativo da conversa
        estado_raw = c.get('state', '')
        mapa_estados = {'closed': 'Fechada', 'open': 'Aberta', 'snoozed': 'Pausada'}
        estado_pt = mapa_estados.get(estado_raw, estado_raw.capitalize())

        # Identifica a origem da primeira mensagem
        source = c.get('source') or {}
        author = source.get('author') or {}
        autor_tipo = author.get('type', '')
        
        if autor_tipo == 'admin':
            origem = "Ativa (Analista)"
        else:
            origem = "Receptiva (Cliente)"

        # NOVA REGRA: Verifica se existe ticket de backoffice atrelado
        tem_ticket = "Não"
        
        objetos_vinculados = c.get('linked_objects', {}).get('data', [])
        for obj in objetos_vinculados:
            if obj.get('type') == 'ticket':
                tem_ticket = "Sim"
                break
                
        if tem_ticket == "Não" and c.get('tickets'):
            tem_ticket = "Sim"

        # Estatísticas de tempo
        stats = c.get('statistics') or {}
        time_reply_sec = stats.get('time_to_admin_reply') or stats.get('response_time')
        time_close_sec = stats.get('time_to_close')
        if not time_close_sec:
            if stats.get('last_close_at') and c.get('created_at'):
                time_close_sec = stats.get('last_close_at') - c.get('created_at')

        # Montagem da linha
        row = {
            "ID": c['id'],
            "timestamp_real": c['created_at'], 
            "Data": (datetime.fromtimestamp(c['created_at']) - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M"),
            "Equipe": nome_equipe,
            "Origem": origem,
            "Estado": estado_pt,
            "Atendente": assignee_name,
            "Link": link,
            "Tempo Resposta (seg)": time_reply_sec,
            "Tempo Resolução (seg)": time_close_sec,
            "Tempo Resposta": format_sla_string(time_reply_sec),
            "Tempo Resolução": format_sla_string(time_close_sec),
            "CSAT Nota": (c.get('conversation_rating') or {}).get('rating'),
            "CSAT Comentario": (c.get('conversation_rating') or {}).get('remark'),
            "Ticket Backoffice": tem_ticket
        }
        
        attrs = c.get('custom_attributes', {})
        for key, value in attrs.items():
            nome_bonito = mapping.get(key)
            if nome_bonito: 
                row[nome_bonito] = value
            else: 
                row[key] = value
        rows.append(row)
    
    df = pd.DataFrame(rows)
    coluna_teimosa = "Motivo 2 (Se houver)"
    if not df.empty and coluna_teimosa not in df.columns:
        df[coluna_teimosa] = None 
        
    if not df.empty:
        df = df.sort_values(by="timestamp_real", ascending=True)
    return df

def gerar_excel_multias(df, colunas_selecionadas):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for col in colunas_selecionadas:
            if col in df.columns and col not in ["Data", "Link", "ID", "Qtd. Atributos"]:
                try:
                    resumo = df[col].value_counts().reset_index()
                    resumo.columns = [col, 'Quantidade']
                    nome_aba = col[:30].replace("/", "-")
                    resumo.to_excel(writer, index=False, sheet_name=nome_aba)
                except: pass

        cols_fixas = ["Data", "Estado", "Atendente", "Tempo Resposta", "Tempo Resolução", "CSAT Nota", "CSAT Comentario", "Link"]
        cols_finais = cols_fixas + [c for c in colunas_selecionadas if c not in cols_fixas]
        cols_existentes = [c for c in cols_finais if c in df.columns]
        df[cols_existentes].to_excel(writer, index=False, sheet_name='Base Completa')
        writer.sheets['Base Completa'].set_column('A:A', 18) 
    return output.getvalue()

# Interface

st.title("📊 Relatório Gerencial: Atributos & SLA")

with st.sidebar:
    st.header("Filtros")
    if st.button("🧹 Limpar Cache"):
        st.cache_data.clear()
        st.success("Limpo!")

    data_hoje = datetime.now()
    periodo = st.date_input("Período", (data_hoje - timedelta(days=7), data_hoje), format="DD/MM/YYYY")
    team_input = st.text_input("IDs dos Times:", value="2975006")
    btn_run = st.button("🚀 Gerar Dados", type="primary")
    logout_button()

if btn_run:
    start, end = periodo
    ids_times = [int(x.strip()) for x in team_input.split(",") if x.strip().isdigit()] if team_input else None
    
    with st.spinner("Analisando dados..."):
        mapa = get_attribute_definitions()
        admins_map = get_all_admins()
        raw = fetch_conversations(start, end, ids_times)
        
        if raw:
            df = process_data(raw, mapa, admins_map)
            st.session_state['df_final'] = df
            st.toast(f"✅ {len(df)} conversas carregadas.")
        else:
            st.warning("Nenhum dado encontrado.")

if 'df_final' in st.session_state:
    df_completo = st.session_state['df_final']
    
    # Cria filtros globais na barra lateral
    with st.sidebar:
        st.markdown("### ⚙️ Filtros Globais")
        
        todos_analistas = sorted(df_completo["Atendente"].astype(str).unique())
        analistas_selecionados = st.multiselect(
            "👤 Analistas:", 
            options=todos_analistas,
            default=todos_analistas,
            help="Remove quem não faz parte da equipa."
        )
        
        todas_origens = sorted(df_completo["Origem"].astype(str).unique())
        origens_selecionadas = st.multiselect(
            "🔄 Origem do Contato:", 
            options=todas_origens,
            default=todas_origens
        )
    
    # Aplica os filtros no dataframe principal
    df = df_completo.copy()
    
    if analistas_selecionados:
        df = df[df["Atendente"].isin(analistas_selecionados)]
        
    if origens_selecionadas:
        df = df[df["Origem"].isin(origens_selecionadas)]
        
    st.divider()
    
    # Seleção de Colunas
    todas_colunas = list(df.columns)
    COL_EXPANSAO = "Expansão (Passagem de bastão para CSM)"
    sugestao = ["Tipo de Atendimento", COL_EXPANSAO, "Motivo de Contato", "Motivo 2 (Se houver)", "Status do atendimento"]
    padrao = [c for c in sugestao if c in todas_colunas]
    ignorar = ["ID", "timestamp_real", "Data", "Link", "Atendente", "CSAT Nota", "CSAT Comentario", "Tempo Resposta (seg)", "Tempo Resolução (seg)", "Tempo Resposta", "Tempo Resolução"]
    
    cols_usuario = st.multiselect("Atributos para análise:", [c for c in todas_colunas if c not in ignorar], default=padrao)

    # KPIs
    st.markdown("### 📌 Resumo")
    
    st.markdown("""
        <style>
        div[data-testid="stMetricValue"] {
            font-size: 1.3rem !important; 
            white-space: normal !important; 
            line-height: 1.2 !important; 
        }
        </style>
    """, unsafe_allow_html=True)
    
    k1, k2, k3, k4, k5 = st.columns(5)
    
    total_conv = len(df)
    preenchidos = df["Motivo de Contato"].notna().sum() if "Motivo de Contato" in df.columns else 0
    resolvidos = df[df["Status do atendimento"] == "Resolvido"].shape[0] if "Status do atendimento" in df.columns else 0
    tempo_med = df["Tempo Resolução (seg)"].mean() if "Tempo Resolução (seg)" in df.columns else 0
    
    top_motivo = "N/A"
    if "Motivo de Contato" in df.columns:
        c = df["Motivo de Contato"].value_counts()
        if not c.empty: top_motivo = c.index[0].split(">")[-1].strip()

    k1.metric("Total Conversas", total_conv)
    k2.metric("Classificados", preenchidos)
    k3.metric("Resolvidos", resolvidos)
    k4.metric("Tempo Médio", format_sla_string(tempo_med))
    k5.metric("Top Motivo", top_motivo)

    st.divider()

    # Menu de Navegação à prova de falhas
    aba_selecionada = st.radio(
        "Navegação", 
        ["📊 Distribuição", "👥 Equipe & Performance", "🔀 Cruzamentos", "🔗 Top Motivos", "⭐ CSAT / DSAT", "⏱️ SLA", "📋 Dados"],
        horizontal=True,
        label_visibility="collapsed"
    )

    if aba_selecionada == "📊 Distribuição":
        # --- NOVO: GRÁFICO DE EVOLUÇÃO NO TEMPO ---
        st.subheader("📈 Evolução de Volume no Tempo")
        
        df_tempo = df.copy()
        # Converte o timestamp para data agrupada por dia
        df_tempo['Data_Agrupamento'] = (pd.to_datetime(df_tempo['timestamp_real'], unit='s') - timedelta(hours=3)).dt.date
        vol_tempo = df_tempo.groupby('Data_Agrupamento').size().reset_index(name='Volume')
        
        fig_bar = px.bar(vol_tempo, x='Data_Agrupamento', y='Volume', text='Volume', title="Volume Diário de Conversas")
        fig_bar.update_xaxes(title="Data", tickformat="%d/%m/%Y")
        st.plotly_chart(fig_bar, use_container_width=True)
        
        st.divider()
        
        # --- GRÁFICOS DE DISTRIBUIÇÃO ORIGINAIS ---
        c_filt1, c_filt2 = st.columns([3, 1])
        with c_filt1:
            graf_sel = st.selectbox("Selecione o Atributo:", cols_usuario, key="sel_graf_dist")
        with c_filt2:
            qtd_dist = st.slider("Qtd. Itens:", 5, 50, 10, key="slider_dist_qtd")

        if cols_usuario:
            c1, c2 = st.columns([2, 1])
            
            df_clean = df[df[graf_sel].notna()]
            contagem = df_clean[graf_sel].value_counts().reset_index()
            contagem.columns = ["Opção", "Qtd"]
            contagem = contagem.head(qtd_dist) 
            
            total_registros = contagem["Qtd"].sum()
            contagem["Label"] = contagem.apply(lambda x: f"{x['Qtd']} ({(x['Qtd']/total_registros*100):.1f}%)", axis=1)
            contagem = contagem.sort_values("Qtd", ascending=False).reset_index(drop=True)

            with c1:
                altura_graf = max(600, len(contagem) * 50) 
                fig = px.bar(contagem, x="Qtd", y="Opção", text="Label", orientation='h', title=f"Distribuição: {graf_sel} (Top {qtd_dist})", height=altura_graf)
                fig.update_layout(yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig, use_container_width=True)
                
            with c2:
                st.write(f"**Ranking (Top {qtd_dist}):**")
                st.dataframe(contagem[["Opção", "Qtd"]], use_container_width=True, hide_index=True)
        else:
            st.warning("Selecione atributos no topo da página.")

    if aba_selecionada == "👥 Equipe & Performance":
        # --- NOVA SEÇÃO: TAXA DE CLASSIFICAÇÃO ---
        st.subheader("🎯 Taxa de Classificação (Conversas Fechadas)")
        
        # Filtra apenas os chamados com o Estado nativo "Fechada" (closed)
        if "Estado" in df.columns:
            df_calc = df[df["Estado"] == "Fechada"].copy()
        else:
            df_calc = df.copy()

        if "Motivo de Contato" in df_calc.columns and not df_calc.empty:
            total_geral = len(df_calc)
            classificados_geral = df_calc["Motivo de Contato"].notna().sum()
            taxa_geral = (classificados_geral / total_geral * 100) if total_geral > 0 else 0
            
            # Métrica geral e Barra de progresso
            st.metric(
                "Taxa Geral da Equipe", 
                f"{taxa_geral:.1f}%", 
                f"{classificados_geral} de {total_geral} conversas fechadas classificadas", 
                delta_color="off"
            )
            st.progress(min(taxa_geral / 100, 1.0))
            
            # Tabela individual por Analista
            resumo_analistas = df_calc.groupby("Atendente").agg(
                Total=('ID', 'count'),
                Classificados=('Motivo de Contato', lambda x: x.notna().sum())
            ).reset_index()
            
            resumo_analistas['Pendentes'] = resumo_analistas['Total'] - resumo_analistas['Classificados']
            resumo_analistas['Taxa (%)'] = (resumo_analistas['Classificados'] / resumo_analistas['Total'] * 100).round(1)
            
            # Ordenar pelo maior volume Total de conversas
            resumo_analistas = resumo_analistas.sort_values(by="Total", ascending=False)
            
            # Adicionar o símbolo de % para apresentar na tabela
            resumo_analistas_view = resumo_analistas.copy()
            resumo_analistas_view['Taxa (%)'] = resumo_analistas_view['Taxa (%)'].apply(lambda x: f"{x}%")
            
            st.write("**Desempenho Individual (Apenas Fechadas):**")
            st.dataframe(resumo_analistas_view, use_container_width=True, hide_index=True)
        else:
            st.warning("Sem dados de conversas fechadas para calcular a taxa ou o atributo 'Motivo de Contato' não existe.")
            
        st.divider()
        
        st.subheader("Volume de Conversas")
        vol = df['Atendente'].value_counts().reset_index()
        vol.columns = ['Agente', 'Volume']
        st.plotly_chart(px.bar(vol, x='Agente', y='Volume', text='Volume', height=500), use_container_width=True)

    if aba_selecionada == "🔀 Cruzamentos":
        qtd_cross = st.slider("Quantidade de itens no Ranking:", 5, 50, 10, key="slider_cross")

        def plot_stack(df_in, x_col, color_col, title, limit=10):
            top_n = df_in[x_col].value_counts().head(limit).index.tolist()
            df_filtered = df_in[df_in[x_col].isin(top_n)]
            g = df_filtered.groupby([x_col, color_col]).size().reset_index(name='Qtd')
            g['Total'] = g.groupby(x_col)['Qtd'].transform('sum')
            g['Pct'] = g.apply(lambda x: f"{(x['Qtd']/x['Total']*100):.0f}%", axis=1)
            h_dyn = max(600, len(top_n) * 50) 
            f = px.bar(g, y=x_col, x='Qtd', color=color_col, text='Pct', orientation='h', title=title, height=h_dyn)
            f.update_layout(yaxis={'categoryorder':'total ascending'})
            return f

        if "Motivo de Contato" in df.columns and "Status do atendimento" in df.columns:
            st.plotly_chart(plot_stack(df.dropna(subset=["Motivo de Contato", "Status do atendimento"]), "Motivo de Contato", "Status do atendimento", "1. Status por Motivo", qtd_cross), use_container_width=True)
        
        st.divider()

        if "Motivo de Contato" in df.columns and "Tipo de Atendimento" in df.columns:
            st.plotly_chart(plot_stack(df.dropna(subset=["Motivo de Contato", "Tipo de Atendimento"]), "Motivo de Contato", "Tipo de Atendimento", "2. Tipo por Motivo", qtd_cross), use_container_width=True)
        
        st.divider()
        
        if "Tipo de Atendimento" in df.columns and "Status do atendimento" in df.columns:
            st.plotly_chart(plot_stack(df.dropna(subset=["Tipo de Atendimento", "Status do atendimento"]), "Tipo de Atendimento", "Status do atendimento", "3. Status por Tipo de atendimento", qtd_cross), use_container_width=True)
        
        st.divider()
        
        st.subheader("🔍 Investigação de Motivos (1 vs 2)")
        
        if "Motivo de Contato" in df.columns and "Motivo 2 (Se houver)" in df.columns:
            # Filtra apenas conversas que têm os dois motivos preenchidos
            df_mots = df.dropna(subset=["Motivo de Contato", "Motivo 2 (Se houver)"])
            
            if not df_mots.empty:
                c_dir1, c_dir2 = st.columns([1, 2])
                with c_dir1:
                    direcao = st.radio("Analisar a partir do:", ["Motivo Principal", "Motivo 2 (Secundário)"])
                
                # Define quem é a origem e quem é o destino com base na escolha
                col_origem = "Motivo de Contato" if "Principal" in direcao else "Motivo 2 (Se houver)"
                col_destino = "Motivo 2 (Se houver)" if "Principal" in direcao else "Motivo de Contato"
                
                with c_dir2:
                    opcoes_origem = sorted(df_mots[col_origem].unique())
                    motivo_selecionado = st.selectbox(f"Selecione um {col_origem} para investigar:", opcoes_origem)
                
                # Filtra os dados apenas para o motivo escolhido
                df_foco = df_mots[df_mots[col_origem] == motivo_selecionado]
                resumo_foco = df_foco[col_destino].value_counts().reset_index()
                resumo_foco.columns = [col_destino, "Quantidade"]
                
                # Calcula a porcentagem para mostrar no gráfico
                total_foco = resumo_foco["Quantidade"].sum()
                resumo_foco["Label"] = resumo_foco["Quantidade"].astype(str) + " (" + (resumo_foco["Quantidade"] / total_foco * 100).round(1).astype(str) + "%)"
                
                # Ajusta a altura dinamicamente para não ficar espremido
                h_foco = max(400, len(resumo_foco) * 45)
                
                fig_foco = px.bar(
                    resumo_foco, 
                    y=col_destino, 
                    x="Quantidade", 
                    orientation="h", 
                    text="Label",
                    title=f"Quando o {col_origem} é '{motivo_selecionado}', estes são as classificações:",
                    height=h_foco,
                    color_discrete_sequence=['#4C51BF'] # Azul profissional, sem arco-íris
                )
                fig_foco.update_layout(yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig_foco, use_container_width=True)
            else:
                st.info("Não há conversas no período filtrado que tenham os dois motivos preenchidos ao mesmo tempo.")

    if aba_selecionada == "🔗 Top Motivos":
        col_m1, col_m2 = "Motivo de Contato", "Motivo 2 (Se houver)"
        if col_m1 in df.columns and col_m2 in df.columns:
            qtd_top = st.slider("Quantidade de Motivos no Ranking:", 5, 50, 10)
            rank = pd.concat([df[col_m1], df[col_m2]]).value_counts().reset_index()
            rank.columns = ["Motivo", "Total"]
            rank_cut = rank.head(qtd_top)
            total_abs = rank["Total"].sum()
            rank_cut["Label"] = rank_cut["Total"].apply(lambda x: f"{x} ({(x/total_abs*100):.1f}%)")
            h_mot = max(600, qtd_top*50)

            fig_glob = px.bar(rank_cut, x="Total", y="Motivo", orientation='h', text="Label", title=f"Top {qtd_top} Motivos de Contato", height=h_mot)
            fig_glob.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig_glob, use_container_width=True)
            
            with st.expander("Ver lista completa"):
                st.dataframe(rank, use_container_width=True)

    if aba_selecionada == "⭐ CSAT / DSAT":
        if "CSAT Nota" not in df.columns:
             st.warning("Sem dados.")
        else:
            df_csat = df.dropna(subset=["CSAT Nota"]).copy()
            if df_csat.empty:
                st.info("Sem avaliações.")
            else:
                k1, k2 = st.columns(2)
                k1.metric("Média Geral CSAT", f"{df_csat['CSAT Nota'].mean():.2f}/5.0")
                k2.metric("Total de Avaliações", len(df_csat))
                
                st.divider()
                
                # Classifica as notas em grupos
                def classificar_nota(nota):
                    if nota >= 4: return "Positiva (4-5)"
                    elif nota == 3: return "Neutra (3)"
                    else: return "Negativa (1-2)"
                    
                df_csat["Tipo de Avaliação"] = df_csat["CSAT Nota"].apply(classificar_nota)
                
                # --- NOVO GRÁFICO GERAL EM ROSCA ---
                st.subheader("Visão Geral das Notas")
                resumo_geral = df_csat["Tipo de Avaliação"].value_counts().reset_index()
                resumo_geral.columns = ["Tipo", "Quantidade"]
                
                cores = {
                    "Positiva (4-5)": "#28a745", 
                    "Neutra (3)": "#ffc107",     
                    "Negativa (1-2)": "#dc3545"  
                }

                fig_pizza = px.pie(
                    resumo_geral, 
                    values="Quantidade", 
                    names="Tipo", 
                    color="Tipo",
                    color_discrete_map=cores,
                    hole=0.4,
                    height=350
                )
                st.plotly_chart(fig_pizza, use_container_width=True)
                
                st.divider()
                
                if "Motivo de Contato" in df_csat.columns:
                    c_conf1, c_conf2 = st.columns([2, 1])
                    with c_conf1:
                        visao_csat = st.selectbox(
                            "O que queres focar agora?", 
                            [
                                "🚨 Foco em DSAT: Motivos com mais avaliações NEGATIVAS", 
                                "⚠️ Foco em Neutras: Motivos com mais avaliações NEUTRAS",
                                "⭐ Foco em CSAT: Motivos com mais avaliações POSITIVAS",
                                "📊 Visão Geral: Motivos com maior volume total"
                            ]
                        )
                    with c_conf2:
                        qtd_csat = st.slider("Qtd. Motivos no Gráfico:", 5, 50, 10, key="slider_csat_qtd")

                    csat_group = df_csat.groupby(["Motivo de Contato", "Tipo de Avaliação"]).size().reset_index(name='Quantidade')
                    total_por_motivo = csat_group.groupby("Motivo de Contato")['Quantidade'].sum().reset_index(name='Total')
                    
                    if "NEGATIVAS" in visao_csat:
                        filtro = csat_group[csat_group["Tipo de Avaliação"] == "Negativa (1-2)"]
                        top_motivos = filtro.sort_values("Quantidade", ascending=False).head(qtd_csat)["Motivo de Contato"].tolist()
                    elif "NEUTRAS" in visao_csat:
                        filtro = csat_group[csat_group["Tipo de Avaliação"] == "Neutra (3)"]
                        top_motivos = filtro.sort_values("Quantidade", ascending=False).head(qtd_csat)["Motivo de Contato"].tolist()
                    elif "POSITIVAS" in visao_csat:
                        filtro = csat_group[csat_group["Tipo de Avaliação"] == "Positiva (4-5)"]
                        top_motivos = filtro.sort_values("Quantidade", ascending=False).head(qtd_csat)["Motivo de Contato"].tolist()
                    else:
                        top_motivos = total_por_motivo.sort_values("Total", ascending=False).head(qtd_csat)["Motivo de Contato"].tolist()
                    
                    df_grafico = csat_group[csat_group["Motivo de Contato"].isin(top_motivos)]
                    
                    if "NEGATIVAS" in visao_csat:
                        ordem = df_grafico[df_grafico["Tipo de Avaliação"] == "Negativa (1-2)"].sort_values("Quantidade", ascending=True)["Motivo de Contato"].tolist()
                    elif "NEUTRAS" in visao_csat:
                        ordem = df_grafico[df_grafico["Tipo de Avaliação"] == "Neutra (3)"].sort_values("Quantidade", ascending=True)["Motivo de Contato"].tolist()
                    elif "POSITIVAS" in visao_csat:
                        ordem = df_grafico[df_grafico["Tipo de Avaliação"] == "Positiva (4-5)"].sort_values("Quantidade", ascending=True)["Motivo de Contato"].tolist()
                    else:
                        ordem = total_por_motivo[total_por_motivo["Motivo de Contato"].isin(top_motivos)].sort_values("Total", ascending=True)["Motivo de Contato"].tolist()
                    
                    para_adicionar = [m for m in top_motivos if m not in ordem]
                    ordem = para_adicionar + ordem
                    
                    h_c = max(400, len(ordem) * 50)
                    
                    fig = px.bar(
                        df_grafico,
                        y="Motivo de Contato",
                        x="Quantidade",
                        color="Tipo de Avaliação",
                        orientation="h",
                        color_discrete_map=cores,
                        title=f"Distribuição Real de Notas (Top {qtd_csat})",
                        height=h_c,
                        text="Quantidade"
                    )
                    
                    fig.update_layout(yaxis={'categoryorder':'array', 'categoryarray': ordem})
                    st.plotly_chart(fig, use_container_width=True)
                    
                    st.divider()
                    
                    st.subheader("📋 Detalhamento por Motivo")
                    tabela_csat = df_csat.groupby("Motivo de Contato").agg(
                        Total_Avaliacoes=('CSAT Nota', 'count'),
                        Notas_Positivas=('CSAT Nota', lambda x: (x >= 4).sum()),
                        Notas_Neutras=('CSAT Nota', lambda x: (x == 3).sum()),
                        Notas_Negativas=('CSAT Nota', lambda x: (x <= 2).sum()),
                    ).reset_index()
                    
                    tabela_csat["% Positivas"] = (tabela_csat["Notas_Positivas"] / tabela_csat["Total_Avaliacoes"] * 100).round(1).astype(str) + "%"
                    tabela_csat["% Neutras"] = (tabela_csat["Notas_Neutras"] / tabela_csat["Total_Avaliacoes"] * 100).round(1).astype(str) + "%"
                    tabela_csat["% Negativas"] = (tabela_csat["Notas_Negativas"] / tabela_csat["Total_Avaliacoes"] * 100).round(1).astype(str) + "%"
                    
                    tabela_csat = tabela_csat[["Motivo de Contato", "Total_Avaliacoes", "Notas_Positivas", "% Positivas", "Notas_Neutras", "% Neutras", "Notas_Negativas", "% Negativas"]]
                    
                    tabela_csat = tabela_csat.sort_values("Total_Avaliacoes", ascending=False)
                    st.dataframe(tabela_csat, use_container_width=True, hide_index=True)
                    
                    st.divider()

                    # --- NOVA SEÇÃO: LEITURA DE COMENTÁRIOS ---
                    st.subheader("💬 Comentários dos Clientes")
                    
                    df_comentarios = df_csat.dropna(subset=["CSAT Comentario"]).copy()
                    
                    if df_comentarios.empty:
                        st.info("Nenhum cliente deixou comentário em texto neste período.")
                    else:
                        filtro_nota = st.selectbox(
                            "Filtrar comentários por tipo:", 
                            ["Apenas Negativos (1 e 2)", "Apenas Neutros (3)", "Apenas Positivos (4 e 5)", "Mostrar Todos"]
                        )
                        
                        if "Negativos" in filtro_nota:
                            df_comentarios = df_comentarios[df_comentarios["CSAT Nota"] <= 2]
                        elif "Neutros" in filtro_nota:
                            df_comentarios = df_comentarios[df_comentarios["CSAT Nota"] == 3]
                        elif "Positivos" in filtro_nota:
                            df_comentarios = df_comentarios[df_comentarios["CSAT Nota"] >= 4]
                            
                        if df_comentarios.empty:
                            st.warning("Nenhum comentário encontrado com este filtro.")
                        else:
                            cols_comentarios = ["Data", "Atendente", "Motivo de Contato", "CSAT Nota", "CSAT Comentario", "Link"]
                            cols_disp_comentarios = [c for c in cols_comentarios if c in df_comentarios.columns]
                            
                            df_comentarios = df_comentarios.sort_values(by="Data", ascending=False)
                            
                            st.dataframe(
                                df_comentarios[cols_disp_comentarios],
                                use_container_width=True,
                                hide_index=True,
                                column_config={
                                    "Link": st.column_config.LinkColumn("Link", display_text="🔗 Abrir"),
                                    "CSAT Comentario": st.column_config.TextColumn("Texto da Avaliação", width="large")
                                }
                            )

    if aba_selecionada == "⏱️ SLA":
        st.header("Análise de Tempo")
        # Novo seletor visual para a equipe
        filtro_ticket = st.radio(
            "Visualizar tempo de resolução de:",
            ["Apenas Atendimentos Normais", "Apenas Conversas com Ticket", "Visão Geral"],
            horizontal=True
        )
        
        # Aplica o filtro na base
        df_t = df.copy()
        if "Ticket Backoffice" in df_t.columns:
            if filtro_ticket == "Apenas Atendimentos Normais":
                df_t = df_t[df_t["Ticket Backoffice"] == "Não"]
            elif filtro_ticket == "Apenas Conversas com Ticket":
                df_t = df_t[df_t["Ticket Backoffice"] == "Sim"]

        col_res = "Tempo Resolução (seg)"
        if col_res in df_t.columns:
            df_t = df_t.dropna(subset=[col_res])
            if not df_t.empty:
                st.subheader("⚡ Velocidade por Agente")
                tag = df_t.groupby("Atendente")[col_res].mean().reset_index().sort_values(col_res)
                tag["Label"] = tag[col_res].apply(format_sla_string)
                f_tag = px.bar(tag, x=col_res, y="Atendente", text="Label", orientation='h', title="Média de Tempo)", height=max(500, len(tag)*50))
                f_tag.update_xaxes(showticklabels=False)
                st.plotly_chart(f_tag, use_container_width=True)
                
                st.divider()
                
                st.subheader("🐢 Motivos mais demorados (Média de Resolução)")
                qtd_sla = st.slider("Qtd. Motivos:", 5, 50, 10, key="slider_sla")
                
                if "Motivo de Contato" in df.columns:
                    t_motivo = df_t.groupby("Motivo de Contato")[col_res].mean().reset_index()
                    t_motivo = t_motivo.sort_values(col_res, ascending=False).head(qtd_sla)
                    t_motivo = t_motivo.sort_values(col_res, ascending=True)
                    t_motivo["Label"] = t_motivo[col_res].apply(format_sla_string)
                    h_dyn = max(600, len(t_motivo) * 50)
                    
                    fig_tm = px.bar(t_motivo, x=col_res, y="Motivo de Contato", text="Label", orientation='h', height=h_dyn, title=f"Top {qtd_sla} Motivos mais demorados")
                    fig_tm.update_xaxes(showticklabels=False)
                    st.plotly_chart(fig_tm, use_container_width=True)
            else: st.warning("Sem dados de tempo.")

    if aba_selecionada == "📋 Dados":
        with st.form("form_filtros_tabela"):
            st.write("🔍 Filtros da Pesquisa")
            # Adicionamos mais uma coluna aqui (c_eq)
            c_eq, c1, c2, c3, c4 = st.columns(5)
            
            with c_eq:
                equipes_unicas = sorted(df["Equipe"].astype(str).unique())
                sel_equipes = st.multiselect("🏢 Equipe (ID):", equipes_unicas)
                
            with c1:
                agentes_unicos = sorted(df["Atendente"].astype(str).unique())
                sel_agentes = st.multiselect("👤 Analista:", agentes_unicos)
                
            with c2:
                if "Tipo de Atendimento" in df.columns:
                    tipos_unicos = sorted(df["Tipo de Atendimento"].dropna().astype(str).unique())
                    sel_tipos = st.multiselect("💬 Tipo:", tipos_unicos)
                else:
                    sel_tipos = []
                    
            with c3:
                opcoes_motivo = set()
                if "Motivo de Contato" in df.columns:
                    opcoes_motivo.update(df["Motivo de Contato"].dropna().astype(str).unique())
                if "Motivo 2 (Se houver)" in df.columns:
                    opcoes_motivo.update(df["Motivo 2 (Se houver)"].dropna().astype(str).unique())
                
                if opcoes_motivo:
                    sel_motivos = st.multiselect("🎯 Motivo:", sorted(list(opcoes_motivo)))
                else:
                    sel_motivos = []
                    
            with c4:
                if "Status do atendimento" in df.columns:
                    status_unicos = sorted(df["Status do atendimento"].dropna().astype(str).unique())
                    sel_status = st.multiselect("🚦 Status:", status_unicos)
                else:
                    sel_status = []

            aplicar = st.form_submit_button("Aplicar Filtros")

        df_view = df.copy()
        
        # Nova regra para aplicar o filtro da equipe
        if sel_equipes:
            df_view = df_view[df_view["Equipe"].isin(sel_equipes)]
            
        if sel_agentes:
            df_view = df_view[df_view["Atendente"].isin(sel_agentes)]
            
        if sel_tipos:
            df_view = df_view[df_view["Tipo de Atendimento"].isin(sel_tipos)]
            
        if sel_motivos:
            condicao1 = df_view["Motivo de Contato"].isin(sel_motivos) if "Motivo de Contato" in df_view.columns else pd.Series(False, index=df_view.index)
            condicao2 = df_view["Motivo 2 (Se houver)"].isin(sel_motivos) if "Motivo 2 (Se houver)" in df_view.columns else pd.Series(False, index=df_view.index)
            df_view = df_view[condicao1 | condicao2]
            
        if sel_status:
            df_view = df_view[df_view["Status do atendimento"].isin(sel_status)]

        c_resumo, c_botao = st.columns([4, 1])
        
        with c_resumo:
            st.caption(f"Exibindo **{len(df_view)}** conversas após os filtros.")
            
        with c_botao:
            excel = gerar_excel_multias(df_view, cols_usuario)
            st.download_button("📥 Baixar Excel", data=excel, file_name="relatorio_filtrado.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", use_container_width=True)
        
        cols_display = ["Data", "Estado", "Equipe", "Atendente", "Link", "Tempo Resolução"] + cols_usuario
        cols_existentes = [c for c in cols_display if c in df_view.columns]
        
        st.dataframe(
            df_view[cols_existentes], 
            use_container_width=True, 
            hide_index=True,
            column_config={
                "Link": st.column_config.LinkColumn("Link", display_text="🔗 Abrir Conversa")
            }
        )
