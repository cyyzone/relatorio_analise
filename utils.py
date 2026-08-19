import streamlit as st
import requests
import time
import pymongo
import datetime
import extra_streamlit_components as stx

def get_cookie_manager():
    return stx.CookieManager(key="auth_cookie_manager")

def check_password():
    """Gerencia a autenticacao via secrets e guarda a sessao em Cookies."""
    if "APP_PASSWORD" not in st.secrets:
        st.error("ERRO: Configure 'APP_PASSWORD' no ficheiro .streamlit/secrets.toml")
        return False

    cookie_manager = get_cookie_manager()
    st.session_state["_cookie_manager"] = cookie_manager
    senha_correta = st.secrets["APP_PASSWORD"]

    # NOVO: Proteção contra o "Cookie Fantasma" no momento do Logout
    if st.session_state.get("logout_requested", False):
        cookie_val = None # Ignora o cookie residual do navegador
    else:
        cookie_val = cookie_manager.get(cookie="monitor_auth")

    if cookie_val == senha_correta:
        return True

    def password_entered():
        senha_digitada = st.session_state.get("password", "")
        
        if senha_digitada == senha_correta:
            st.session_state["password_correct"] = True
            
            # NOVO: Limpa a flag de logout quando o usuário faz um novo login
            st.session_state["logout_requested"] = False 
            
            validade = datetime.datetime.now() + datetime.timedelta(days=30)
            cookie_manager.set("monitor_auth", senha_correta, expires_at=validade)
            
            if "password" in st.session_state:
                del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    st.text_input(
        "🔒 Digite a senha de acesso:", 
        type="password",
        on_change=password_entered,
        key="password"
    )
    
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("😕 Senha incorreta.")
        
    return False

def logout_button():
    """Desenha um botão de sair na barra lateral"""
    st.sidebar.markdown("---") 
    
    if st.sidebar.button("🚪 Sair do Sistema"):
        if "_cookie_manager" in st.session_state:
            st.session_state["_cookie_manager"].delete("monitor_auth")
        
        st.session_state["password_correct"] = False
        
        # NOVO: Ativa a flag para forçar o sistema a ignorar o cookie na próxima leitura
        st.session_state["logout_requested"] = True
        
        st.rerun()

# ==========================================
# FUNÇÕES DE API E INTEGRAÇÕES
# ==========================================

def make_api_request(method, url, json=None, params=None, max_retries=3):
    """
    Faz chamadas API seguras respeitando o Rate Limit do Intercom.
    Usa o header 'X-RateLimit-Reset' para espera inteligente.
    Se o Intercom disser "PARE" (Erro 429), aguardamos o tempo certo em vez de insistir.
    """
    token = st.secrets.get("INTERCOM_TOKEN", "")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    for attempt in range(max_retries):
        try:
            if method.upper() == "POST":
                response = requests.post(url, json=json, params=params, headers=headers)
            else:
                response = requests.get(url, params=params, headers=headers)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                reset_time = response.headers.get("X-RateLimit-Reset")
                
                if reset_time:
                    try:
                        wait_seconds = int(reset_time) - int(time.time()) + 1
                    except ValueError:
                        wait_seconds = (2 ** attempt) + 1
                else:
                    wait_seconds = (2 ** attempt) + 1
                
                wait_seconds = max(1, wait_seconds)
                st.toast(f"⏳ API cheia. Aguardando {wait_seconds}s para o reset...", icon="🛑")
                time.sleep(wait_seconds)
                continue
            
            else:
                print(f"Erro API {response.status_code}: {response.text}")
                return None
                
        except Exception as e:
            print(f"Erro de Conexao: {e}")
            return None
            
    st.error("Falha na conexao com a API apos varias tentativas.")
    return None

def send_slack_alert(message):
    """Envia notificacao para o Slack se o webhook estiver configurado."""
    webhook = st.secrets.get("SLACK_WEBHOOK")
    
    if not webhook:
        print("❌ ERRO: Webhook do Slack nao encontrado nos secrets.") 
        return

    payload = {"text": message}
    
    try:
        requests.post(webhook, json=payload)
    except Exception as e:
        print(f"Erro ao enviar alerta Slack: {e}")

# ==========================================
# FUNÇÕES DE BANCO DE DADOS (MONGO)
# ==========================================

@st.cache_resource
def init_mongo_connection():
    """Conecta ao MongoDB Atlas usando a URI dos secrets."""
    try:
        uri = st.secrets["MONGO_URI"]
        client = pymongo.MongoClient(uri)
        client.admin.command('ping')
        return client
    except Exception as e:
        st.error(f"Erro ao conectar no MongoDB: {e}")
        return None

def salvar_lote_tickets_mongo(lista_tickets):
    """Salva/Atualiza uma lista de tickets no MongoDB."""
    client = init_mongo_connection()
    if not client: return 0
    
    db = client["suporte_db"]
    collection = db["tickets"]
    
    operacoes = []
    for ticket in lista_tickets:
        op = pymongo.UpdateOne(
            {"id": ticket["id"]}, 
            {"$set": ticket}, 
            upsert=True
        )
        operacoes.append(op)
    
    if operacoes:
        resultado = collection.bulk_write(operacoes)
        return resultado.upserted_count + resultado.modified_count
    return 0

def carregar_tickets_mongo(termo_busca=None):
    """
    Traz tickets. Se termo_busca for None, traz TODOS.
    """
    client = init_mongo_connection()
    if not client: return []
    
    db = client["suporte_db"]
    collection = db["tickets"]
    
    filtro = {}
    
    if termo_busca and str(termo_busca).strip() != "":
        termo_str = str(termo_busca).strip()
        regex_busca = {"$regex": termo_str, "$options": "i"}
        
        filtro = {
            "$or": [
                {"id_interno": termo_str},
                {"cliente": regex_busca},
                {"autor_nome": regex_busca},
                {"autor_email": regex_busca},
                {"id": termo_str}
            ]
        }
    
    cursor = collection.find(filtro, {"_id": 0}).sort("updated_at", -1).limit(1000)
    return list(cursor)
