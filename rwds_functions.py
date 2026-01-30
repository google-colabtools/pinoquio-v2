import os, sys, time, re, shutil
from math import e
import subprocess
import threading
import requests
import json
from huggingface_hub import HfApi
from dotenv import load_dotenv
#planilhas
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import dns.resolver, socket
from urllib.parse import urlparse
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# SISTEMA DE TIMEOUT DE INATIVIDADE (COM REINÍCIO)
# Implementado mecanismo que encerra automaticamente bots que ficarem
# mais de 30 minutos sem produzir saída (sem ações).
# - Monitora timestamp da última atividade de cada bot
# - Captura e armazena a última mensagem de atividade
# - Verifica timeout tanto na saída quanto em verificação ativa
# - TENTA REINICIAR 1x antes de encerrar definitivamente por inatividade
# - Apenas na 2ª detecção de inatividade o bot é encerrado permanentemente
# - Envia notificação Discord informando o encerramento + última atividade
# - Estado: 'inactive_timeout' para bots encerrados por inatividade

# Lock global para sincronização de acesso ao dicionário processes
processes_lock = threading.Lock()


# Configuração de delay entre inicialização de bots (em segundos)
BOT_START_DELAY_SECONDS = 10  # Delay progressivo entre bots (0, 10, 20, 30 segundos, etc.)

def load_json_with_comments(file_path):
    """
    Carrega um arquivo JSON que pode conter comentários // ou /* */
    Remove comentários antes de fazer o parse
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Remove comentários de linha única //
        lines = content.split('\n')
        cleaned_lines = []
        for line in lines:
            # Remove comentários // mas preserva URLs
            if '//' in line and not ('http://' in line or 'https://' in line):
                line = line.split('//')[0]
            cleaned_lines.append(line)
        
        content = '\n'.join(cleaned_lines)
        
        # Remove comentários de bloco /* */
        import re
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        
        # Remove vírgulas extras antes de } ou ]
        content = re.sub(r',\s*([}\]])', r'\1', content)
        
        return json.loads(content)
    except Exception as e:
        print(f"❌ Erro ao carregar JSON com comentários: {str(e)}")
        return None

def extract_email_from_accounts(accounts_data):
    """
    Extrai o email do arquivo accounts.json, suportando ambos os formatos:
    - Formato novo: array direto [{'email': '...'}]
    - Formato antigo: com wrapper {'accounts': [{'email': '...'}]}
    """
    try:
        if isinstance(accounts_data, dict) and 'accounts' in accounts_data:
            # Formato antigo: {'accounts': [...]}
            accounts_list = accounts_data['accounts']
        elif isinstance(accounts_data, list):
            # Formato novo: [...]
            accounts_list = accounts_data
        else:
            return 'Unknown'
        
        if accounts_list and len(accounts_list) > 0:
            return accounts_list[0].get('email', 'Unknown')
        return 'Unknown'
    except Exception:
        return 'Unknown'

#===============================================================

# Carrega o arquivo .env
load_dotenv("configs.env")

bot_acc_env = str(os.getenv("BOT_ACCOUNT", "")).strip()
socks_proxy_env = str(os.getenv("SOCKS_PROXY", "False")).strip().lower() == "true"
discord_webhook_log_env = os.getenv("DISCORD_WEBHOOK_URL_LOG", "").strip()

SOCKS_PROXY = socks_proxy_env
# TODOIST
todoist_api_env = str(os.getenv("TODOIST_API", "")).strip()
TODOIST_API_TOKEN = todoist_api_env

# Define o nome base dos diretórios dos bots (facilita mudanças futuras)
BOT_BASE_DIR_NAME = "pinoquio-v2"
BOT_ZIP_FILE_NAME = f"{BOT_BASE_DIR_NAME}-main.zip"

#==============================================================

#ATUALIZAÇÃO DE PLANILHA
bot_directory_env = str(os.getenv("BOT_DIRECTORY", "")).strip()
SPREADSHEET_ID_env = str(os.getenv("SPREADSHEET_ID", "")).strip()
EMAIL_COLUMN_env = str(os.getenv("EMAIL_COLUMN", "")).strip()
POINTS_COLUMN_env = str(os.getenv("POINTS_COLUMN", "")).strip()


BOT_DIRECTORY = bot_directory_env
# Caminho para o arquivo JSON da sua Service Account
SERVICE_ACCOUNT_FILE = r'serviceaccount.json'
SERVICE_ACCOUNT_URL = f'{BOT_DIRECTORY}{SERVICE_ACCOUNT_FILE}'


# O ID da sua planilha (você encontra na URL da planilha)
SPREADSHEET_ID = SPREADSHEET_ID_env
EMAIL_COLUMN = EMAIL_COLUMN_env
POINTS_COLUMN = POINTS_COLUMN_env


SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

def curl_with_proxy_fallback(url, output, host="127.0.0.1", port=3128, timeout=2):
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            # Try with proxy first if available
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    print(f"🔗 Usando bypass para download: {url}")
                    cmd = f'curl --connect-timeout 30 --max-time 60 --retry 3 -o "{output}" "{url}" --proxy {host}:{port}'
            except Exception:
                print(f"🌐 Usando conexão direta para: {url}")
                cmd = f'curl --connect-timeout 30 --max-time 60 --retry 3 -o "{output}" "{url}"'
            
            # Execute the command
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"✅ Successfully downloaded: {url}")
                return
            else:
                print(f"⚠️ Attempt {attempt + 1}/{max_retries} failed: {result.stderr}")
                if attempt < max_retries - 1:
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    
        except Exception as e:
            print(f"⚠️ Exception on attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
    
    # If all attempts failed, raise the last error
    raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)

def get_sheets_service():
    """Autentica com a Service Account e retorna o serviço da API do Google Sheets."""
    try:
        if os.path.exists(SERVICE_ACCOUNT_FILE):
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )
        elif SERVICE_ACCOUNT_URL:
            try:
                curl_with_proxy_fallback(SERVICE_ACCOUNT_URL, SERVICE_ACCOUNT_FILE)
            except Exception as e:
                print(f"⚠️ Falha ao baixar serviceaccount.json: {e}")
                return None
            # Usa o arquivo baixado para autenticação
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )
        else:
            print("Arquivo serviceaccount.json não encontrado e nenhuma URL fornecida.")
            return None

        service = build('sheets', 'v4', credentials=creds)
        return service
    except Exception as e:
        print(f"Erro durante autenticação ou construção do serviço Google Sheets: {type(e).__name__}: {e}")
        return None

def find_row_by_email(service, sheet_name, target_email):
    """
    Encontra o número da linha de um e-mail específico na planilha.
    Retorna o número da linha (base 1) ou None se não encontrado.
    """
    try:
        range_to_read = f'{sheet_name}!{EMAIL_COLUMN}:{EMAIL_COLUMN}'
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_to_read
        ).execute()
        values = result.get('values', [])
        if not values:
            return None
        for i, row in enumerate(values):
            if row and row[0].strip().lower() == target_email.strip().lower():
                return i + 1
        return None
    except Exception:
        return None

def append_email_and_points(service, sheet_name, email, points):
    """
    Adiciona um novo e-mail e pontos na próxima linha em branco.
    """
    range_to_append = f'{sheet_name}!{EMAIL_COLUMN}:{POINTS_COLUMN}'
    values = [[email, points]]
    body = {'values': values}
    try:
        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=range_to_append,
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
    except Exception:
        pass

def update_points_by_email(email_to_update, new_points, sheet_name):
    """
    Atualiza a coluna de pontos para um e-mail específico na planilha.
    Se o e-mail não existir, adiciona na próxima linha em branco.
    """
    service = get_sheets_service()
    if not service:
        return

    # Garante que o valor seja numérico
    try:
        numeric_points = int(new_points)
    except (ValueError, TypeError):
        try:
            numeric_points = float(new_points)
        except (ValueError, TypeError):
            numeric_points = 0  # fallback seguro

    row_number = find_row_by_email(service, sheet_name, email_to_update)

    if row_number:
        range_to_update = f'{sheet_name}!{POINTS_COLUMN}{row_number}'
        values = [[numeric_points]]
        body = {'values': values}
        try:
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=range_to_update,
                valueInputOption='RAW',
                body=body
            ).execute()
        except Exception:
            pass
    else:
        append_email_and_points(service, sheet_name, email_to_update, numeric_points)
#==============================================================

# Define o basedir como o diretório atual de execução
BASEDIR = os.getcwd()

# Adicionar no início do arquivo, junto com as outras variáveis globais
bot_pids = {
    'A': [],
    'B': [],
    'C': [],
    'D': [],
    'E': []
}
is_shutdown_requested = False  # Nova variável global para controlar o estado de desligamento

# Lista global para rastrear bots com contas banidas
banned_bots = set()  # Conjunto para evitar duplicatas

last_alerts = {}
last_banned_alerts = {}  # Novo: controle de duplicação para alertas de contas banidas

def clean_account_proxys(account_file):
    try:
        # Abre o arquivo e carrega o conteúdo JSON
        with open(account_file, 'r', encoding='utf-8') as f:
            dados = json.load(f)
        
        # Detectar formato: wrapper ou array direto
        if isinstance(dados, dict) and 'accounts' in dados:
            # Formato antigo: {'accounts': [...]}
            accounts_list = dados['accounts']
        elif isinstance(dados, list):
            # Formato novo: [...]
            accounts_list = dados
        else:
            print(f"Formato inválido no arquivo {account_file}")
            return
        
        # Modifica o campo 'proxy' para cada item na lista
        for item in accounts_list:
            if 'proxy' in item:
                item['proxy']['url'] = "127.0.0.1"
                item['proxy']['port'] = 3128
                item['proxy']['username'] = ""
                item['proxy']['password'] = ""
        
        # Salva o arquivo de volta com as alterações
        with open(account_file, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=4)

        print(f"['{account_file}'] Proxy local ativado para {account_file} com sucesso.")

    except Exception as e:
        print(f"Ocorreu um erro: {e}")

def set_socks_proxy(account_file):
    try:
        # Abre o arquivo e carrega o conteúdo JSON
        with open(account_file, 'r', encoding='utf-8') as f:
            dados = json.load(f)
        
        # Detectar formato: wrapper ou array direto
        if isinstance(dados, dict) and 'accounts' in dados:
            # Formato antigo: {'accounts': [...]}
            accounts_list = dados['accounts']
        elif isinstance(dados, list):
            # Formato novo: [...]
            accounts_list = dados
        else:
            print(f"Formato inválido no arquivo {account_file}")
            return
        
        # Modifica o campo 'proxy' para cada item na lista
        for item in accounts_list:
            if 'proxy' in item:
                item['proxy']['url'] = "127.0.0.1"
                item['proxy']['port'] = 8099
                item['proxy']['username'] = ""
                item['proxy']['password'] = ""
        
        # Salva o arquivo de volta com as alterações
        with open(account_file, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=4)

        print(f"['{account_file}'] Proxy SOCKS_TO_HTTP ativado para {account_file} com sucesso.")

    except Exception as e:
        print(f"Ocorreu um erro: {e}")

# Variável global para DNS customizado, fallback para 8.8.8.8 e 1.1.1.1
CUSTOM_DNS_SERVERS = [
    os.getenv("CUSTOM_DNS_SERVER_PRIMARY", "8.8.8.8"),
    os.getenv("CUSTOM_DNS_SERVER_SECONDARY", "1.1.1.1")
]

def resolve_domain(domain, dns_servers=None):
    resolver = dns.resolver.Resolver()
    servers = dns_servers or CUSTOM_DNS_SERVERS
    last_exception = None
    for dns_server in servers:
        try:
            resolver.nameservers = [dns_server]
            answer = resolver.resolve(domain, 'A')
            return answer[0].to_text()
        except Exception as e:
            last_exception = e
            continue
    raise last_exception or Exception("DNS resolution failed")

def post_discord_with_custom_dns(webhook_url, data, dns_servers=None):
    parsed = urlparse(webhook_url)
    ip = resolve_domain(parsed.hostname, dns_servers or CUSTOM_DNS_SERVERS)
    url_with_ip = webhook_url.replace(parsed.hostname, ip)
    headers = {"Host": parsed.hostname, "Content-Type": "application/json"}
    # Desabilita a verificação SSL (workaround)
    return requests.post(url_with_ip, headers=headers, json=data, verify=False)

def send_discord_redeem_alert(bot_letter, message, discord_webhook_url_br, discord_webhook_url_us):
    """Envia uma mensagem para o webhook do Discord"""
    try:
        # Tentar obter o email da conta do arquivo accounts.json
        email = "Unknown"
        session_profile = "Unknown"
        try:
            accounts_file = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_letter}", "src", "accounts.json")
            config_file = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_letter}", "src", "config.json")
            
            # Obter email
            if os.path.exists(accounts_file):
                accounts_data = load_json_with_comments(accounts_file)
                if accounts_data:
                    email = extract_email_from_accounts(accounts_data)
            
            # Obter perfil da sessão e doDailySet
            check_restrict = "Unknown"
            if os.path.exists(config_file):
                config_data = load_json_with_comments(config_file)
                if config_data:
                    session_path = config_data.get('sessionPath', '')
                    if session_path and 'sessions/_' in session_path:
                        session_profile = session_path.split('sessions/_')[1]
                    check_restrict = config_data.get("workers", {}).get("doDesktopSearch", "Unknown")
        except Exception as e:
            print(f"❌ Erro ao obter informações da conta: {str(e)}")
        
        is_multi_br = session_profile.startswith('multi-BR')
        
        if is_multi_br:
            DISCORD_WEBHOOK_URL = discord_webhook_url_br
            SHEET_NAME = 'REWARDS-BR'
        else:
            DISCORD_WEBHOOK_URL = discord_webhook_url_us
            SHEET_NAME = 'REWARDS-US'

        # Extrair apenas o valor numérico dos pontos da mensagem
        points = "0"
        points_int = 0

        if "Current point count:" in message and "Current total:" not in message:
            # Extrai pontos do "Current point count:"
            message = message.strip()
            points_text = message.split("Current point count:")[1].strip()
            points = ''.join(filter(str.isdigit, points_text))
            points_int = int(points) if points else 0
            print(f"📊 CPC Atualizando Planilha: {points_int} para o email: {email}")
            update_points_by_email(email, points, SHEET_NAME)
            return

        elif "Current total:" in message and "Current point count:" not in message:
            # Extrai pontos do "Current total:"
            message = message.strip()
            total_text = message.split("Current total:")[1].strip()
            points = ''.join(filter(str.isdigit, total_text))
            points_int = int(points) if points else 0
            print(f"📊 CT: Atualizando Planilha: {points_int} para o email: {email}")
            update_points_by_email(email, points, SHEET_NAME)


        # Verificar condições para envio da mensagem        
        should_send = (is_multi_br and points_int > 6710) or (not is_multi_br and points_int >= 6500)

            
        # Se doDesktopSearch for False, não envia mensagem
        if not check_restrict:
            print("🔕 Conta em Modo Restrição, nenhuma mensagem será enviada.")
            return

        alert_key = f"{session_profile}-{email}"
        if last_alerts.get(alert_key) == points:
            print(f"🔁 Alerta duplicado ignorado para {alert_key} ({points} pontos)")
            return
        last_alerts[alert_key] = points

        if should_send:
            # Formatar a mensagem com o email, perfil e pontos
            current_time = time.strftime("%d/%m/%Y")
            flag_emoji = ":flag_br:" if is_multi_br else ":flag_us:"
            discord_message = f"{flag_emoji} {current_time}: [{session_profile}-{bot_letter}] - {email} - {points} pontos."
            data = {
                "content": discord_message
            }
            response = post_discord_with_custom_dns(DISCORD_WEBHOOK_URL, data)
            if response.status_code == 204:
                print(f"✅ Alerta enviado para o Discord: {email} [{session_profile}-{bot_letter}] - {points} pontos")
            else:
                print(f"❌ Erro ao enviar alerta para o Discord: {response.status_code}")
        else:
            print(f"ℹ️ Pontuação atual ({points}) não atingiu o limite para envio de alerta ({6710 if is_multi_br else 6500} pontos)")
    except Exception as e:
        print(f"❌ Erro ao enviar alerta para o Discord: {str(e)}")

def send_discord_timeout_alert(bot_letter, discord_webhook_url_br, discord_webhook_url_us, last_message="Nenhuma atividade recente"):
    """Envia uma mensagem para o webhook do Discord quando um bot é encerrado por timeout de inatividade"""
    try:
        # Obter informações da conta
        email = "Unknown"
        session_profile = "Unknown"
        try:
            accounts_file = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_letter}", "src", "accounts.json")
            config_file = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_letter}", "src", "config.json")
            
            # Obter email
            if os.path.exists(accounts_file):
                accounts_data = load_json_with_comments(accounts_file)
                if accounts_data:
                    email = extract_email_from_accounts(accounts_data)
            
            # Obter perfil da sessão
            if os.path.exists(config_file):
                config_data = load_json_with_comments(config_file)
                if config_data:
                    session_path = config_data.get('sessionPath', '')
                    if session_path and 'sessions/_' in session_path:
                        session_profile = session_path.split('sessions/_')[1]
        except Exception as e:
            print(f"❌ Erro ao obter informações da conta: {str(e)}")
        
        # Determinar webhook baseado no perfil
        is_multi_br = session_profile.startswith('multi-BR')
        DISCORD_WEBHOOK_URL = discord_webhook_url_br if is_multi_br else discord_webhook_url_us
        
        # Formatar mensagem para Discord com última atividade
        current_timestamp = time.strftime("%d/%m/%Y %H:%M:%S")
        flag_emoji = ":flag_br:" if is_multi_br else ":flag_us:"
        discord_message = f"⏰ {flag_emoji} {current_timestamp}: [{session_profile}-{bot_letter}] - {email} - ENCERRADO por inatividade (30+ min sem ações)\n📝 Última atividade: {last_message}"
        
        # Enviar mensagem
        data = {"content": discord_message}
        response = post_discord_with_custom_dns(DISCORD_WEBHOOK_URL, data)
        if response.status_code == 204:
            print(f"✅ Notificação de timeout enviada para Discord: {email} [{session_profile}-{bot_letter}]")
            return True
        else:
            print(f"❌ Erro ao enviar notificação de timeout: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Erro ao enviar notificação de timeout para Discord: {str(e)}")
        return False

def send_discord_max_restart_alert(bot_letter, discord_webhook_url_br, discord_webhook_url_us, max_restarts, last_error="Erro não especificado"):
    """Envia uma mensagem para o webhook do Discord quando um bot atinge o número máximo de restarts"""
    try:
        # Obter informações da conta
        email = "Unknown"
        session_profile = "Unknown"
        try:
            accounts_file = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_letter}", "src", "accounts.json")
            config_file = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_letter}", "src", "config.json")
            
            # Obter email
            if os.path.exists(accounts_file):
                accounts_data = load_json_with_comments(accounts_file)
                if accounts_data:
                    email = extract_email_from_accounts(accounts_data)
            
            # Obter perfil da sessão
            if os.path.exists(config_file):
                config_data = load_json_with_comments(config_file)
                if config_data:
                    session_path = config_data.get('sessionPath', '')
                    if session_path and 'sessions/_' in session_path:
                        session_profile = session_path.split('sessions/_')[1]
        except Exception as e:
            print(f"❌ Erro ao obter informações da conta: {str(e)}")
        
        # Determinar webhook baseado no perfil
        is_multi_br = session_profile.startswith('multi-BR')
        DISCORD_WEBHOOK_URL = discord_webhook_url_br if is_multi_br else discord_webhook_url_us
        
        # Formatar mensagem para Discord
        current_timestamp = time.strftime("%d/%m/%Y %H:%M:%S")
        flag_emoji = ":flag_br:" if is_multi_br else ":flag_us:"
        discord_message = f"🔄❌ {flag_emoji} {current_timestamp}: [{session_profile}-{bot_letter}] - {email} - ENCERRADO após {max_restarts} restarts\n📝 Último erro: {last_error}"
        
        # Enviar mensagem
        data = {"content": discord_message}
        response = post_discord_with_custom_dns(DISCORD_WEBHOOK_URL, data)
        if response.status_code == 204:
            print(f"✅ Notificação de max restart enviada para Discord: {email} [{session_profile}-{bot_letter}]")
            return True
        else:
            print(f"❌ Erro ao enviar notificação de max restart: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Erro ao enviar notificação de max restart para Discord: {str(e)}")
        return False

def delete_bot_cookies(bot_letter):
    """Deleta os arquivos de cookies de um bot específico baseado no email da conta"""
    try:
        config_file = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_letter}", "src", "config.json")
        accounts_file = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_letter}", "src", "accounts.json")
        
        if not os.path.exists(config_file):
            print(f"❌ Arquivo config.json não encontrado para Bot {bot_letter}")
            return False
        
        if not os.path.exists(accounts_file):
            print(f"❌ Arquivo accounts.json não encontrado para Bot {bot_letter}")
            return False
        
        config_data = load_json_with_comments(config_file)
        if not config_data:
            print(f"❌ Não foi possível carregar config.json do Bot {bot_letter}")
            return False
        
        # Obter o email da conta
        accounts_data = load_json_with_comments(accounts_file)
        if not accounts_data:
            print(f"❌ Não foi possível carregar accounts.json do Bot {bot_letter}")
            return False
        
        email = extract_email_from_accounts(accounts_data)
        if email == 'Unknown' or not email:
            print(f"❌ Não foi possível identificar o email da conta para Bot {bot_letter}")
            return False
        
        # Obter BOT_ACCOUNT do .env ou extrair do sessionPath
        bot_account = bot_acc_env  # Usa a variável global BOT_ACCOUNT do .env
        
        if not bot_account:
            session_path = config_data.get('sessionPath', '')
            if session_path and 'sessions/_' in session_path:
                session_profile = session_path.split('sessions/_')[1]
                # Tentar extrair do session_profile (remover números finais)
                match = re.match(r'^(.*?)\d*$', session_profile)
                if match:
                    bot_account = match.group(1).rstrip('0123456789')
        
        if not bot_account:
            print(f"❌ Não foi possível identificar o BOT_ACCOUNT para Bot {bot_letter}")
            return False
        
        # Caminho do diretório de cookies: _shared/sessions/_{bot_account}/{email}
        cookies_dir = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_shared", "sessions", f"_{bot_account}", email)
        
        if os.path.exists(cookies_dir):
            # Deletar todos os arquivos de cookies no diretório
            deleted_files = []
            for filename in os.listdir(cookies_dir):
                file_path = os.path.join(cookies_dir, filename)
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        deleted_files.append(filename)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                        deleted_files.append(f"{filename}/")
                except Exception as e:
                    print(f"⚠️ Erro ao deletar {file_path}: {e}")
            
            if deleted_files:
                print(f"🗑️ Cookies deletados para Bot {bot_letter} [{email}]: {', '.join(deleted_files)}")
                return True
            else:
                print(f"⚠️ Nenhum arquivo de cookie encontrado em {cookies_dir}")
                return False
        else:
            print(f"⚠️ Diretório de cookies não encontrado: {cookies_dir}")
            return False
            
    except Exception as e:
        print(f"❌ Erro ao deletar cookies do Bot {bot_letter}: {str(e)}")
        return False

def send_discord_suspension_alert(bot_letter, discord_webhook_url_br, discord_webhook_url_us):
    """Envia uma mensagem para o webhook do Discord quando uma conta é suspensa"""
    global banned_bots, last_banned_alerts
    
    try:
        # Tentar obter o email da conta do arquivo accounts.json
        email = "Unknown"
        session_profile = "Unknown"
        try:
            accounts_file = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_letter}", "src", "accounts.json")
            config_file = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_letter}", "src", "config.json")
            
            # Obter email
            if os.path.exists(accounts_file):
                accounts_data = load_json_with_comments(accounts_file)
                if accounts_data:
                    email = extract_email_from_accounts(accounts_data)
            
            # Obter perfil da sessão e doDesktopSearch
            if os.path.exists(config_file):
                config_data = load_json_with_comments(config_file)
                if config_data:
                    session_path = config_data.get('sessionPath', '')
                    if session_path and 'sessions/_' in session_path:
                        session_profile = session_path.split('sessions/_')[1]
        except Exception as e:
            print(f"❌ Erro ao obter informações da conta: {str(e)}")
        
        # Criar chave única para evitar duplicação
        alert_key = f"{session_profile}-{bot_letter}-{email}"
        
        # Verificar se já foi enviado um alerta para esta combinação
        if alert_key in last_banned_alerts:
            print(f"🔁 Alerta de banimento duplicado ignorado para {alert_key}")
            return
        
        # Registrar que o alerta foi enviado
        last_banned_alerts[alert_key] = True
        
        # Adicionar o bot à lista de banidos
        banned_bots.add(bot_letter)
        print(f"🚫 Bot {bot_letter} adicionado à lista de contas banidas. Não será reiniciado automaticamente.")
        
        # Formatar a mensagem com o email e perfil
        current_time = time.strftime("%d/%m/%Y")
        is_multi_br = session_profile.startswith('multi-BR')
        flag_emoji = ":flag_br:" if is_multi_br else ":flag_us:"
        discord_message = f"⚠️ {flag_emoji} {current_time}: [{session_profile}-{bot_letter}] - {email} - CONTA BANIDA!!"
        
        if is_multi_br:
            DISCORD_WEBHOOK_URL = discord_webhook_url_br
        else:
            DISCORD_WEBHOOK_URL = discord_webhook_url_us

        data = {
            "content": discord_message
        }
        response = post_discord_with_custom_dns(DISCORD_WEBHOOK_URL, data)
        if response.status_code == 204:
            print(f"✅ Alerta de suspensão enviado para o Discord: {email} [{session_profile}-{bot_letter}]")
        else:
            print(f"❌ Erro ao enviar alerta de suspensão para o Discord: {response.status_code}")
    except Exception as e:
        print(f"❌ Erro ao enviar alerta de suspensão para o Discord: {str(e)}")

def check_location():
    ipinfo_url = "https://ipinfo.io"

    try:
        response = requests.get(ipinfo_url)
        data = response.json()

        country = data.get('country')
        ip = data.get('ip', 'Unknown')

        if country != 'US':
            raise EnvironmentError(f"This VM (IP: {ip}) is located outside of the USA. Current country: {country}")
        else:
            print(f"This VM (IP: {ip}) is located in the USA.")

    except requests.RequestException as e:
        raise RuntimeError(f"Failed to retrieve location information for IP: {ip}") from e

def get_current_ip():
    """
    Função para verificar o IP atual usando apenas bibliotecas padrão
    Tenta múltiplos serviços para garantir confiabilidade
    """
    import urllib.request
    import json
    
    try:
        # Tenta obter o IP de diferentes serviços
        services = [
            "https://api.ipify.org?format=json",
            "https://httpbin.org/ip",
            "https://jsonip.com"
        ]
        
        for service in services:
            try:
                with urllib.request.urlopen(service, timeout=10) as response:
                    data = json.loads(response.read().decode())
                    
                    # Extrai o IP baseado na estrutura de resposta de cada serviço
                    if 'ip' in data:
                        current_ip = data['ip']
                    elif 'origin' in data:
                        current_ip = data['origin']
                    else:
                        continue
                    
                    print(f"🌐 IP atual: {current_ip}")
                    return current_ip
            except Exception:
                continue
        
        print("❌ Não foi possível obter o IP de nenhum serviço")
        return None
        
    except Exception as e:
        print(f"❌ Erro ao verificar IP: {e}")
        return None

def setup_ricronus_and_directories(BOT_DIRECTORY):
    """Configura o ricronus e cria os diretórios necessários"""
    curl_with_proxy_fallback(f"{BOT_DIRECTORY}r_rewards.conf", f"{BASEDIR}/ricronus.conf")
    for letter in ["A", "B", "C", "D", "E"]:
        sessions_dir = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{letter}", "dist", "browser", "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        print(f"✅ Diretório criado: {sessions_dir}")

def download_and_extract_bot_A(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE):
    bot_id = "A"
    bot_dir = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_id}")
    print(f"\n--- Iniciando configuração para Bot {bot_id} ---")
    print(f"Diretório alvo: {bot_dir}")

    original_cwd = os.getcwd()
    try:
        if not os.path.isdir(bot_dir):
            print(f"⚠️ ERRO: Diretório {bot_dir} não encontrado. Pulando Bot {bot_id}.")
            return

        os.chdir(bot_dir)
        print(f"Diretório de trabalho alterado para: {os.getcwd()}")

        zip_file_name = BOT_ZIP_FILE_NAME
        download_url = f"{BOT_DIRECTORY}{BOT_ACCOUNT}_{bot_id}.zip"

        print(f"Baixando {download_url} para {zip_file_name}...")
        curl_with_proxy_fallback(download_url, zip_file_name)

        print(f"Extraindo {zip_file_name}...")
        subprocess.run(f"unzip -o {zip_file_name}", shell=True, check=True)

        print(f"Removendo {zip_file_name}...")
        subprocess.run(f"rm -f {zip_file_name}", shell=True, check=True)

        if CONFIG_MODE == "GEN_COOKIE_CONFIG":
            print("Aplicando proxy local para geração de cookies...")
            clean_account_proxys("src/accounts.json")
        
        if CONFIG_MODE == "DEFAULT_CONFIG_US":
            print("Aplicando proxy local para configuração padrão dos EUA...")
            clean_account_proxys("src/accounts.json")

        if SOCKS_PROXY == True:
            print("Ativando proxy SOCKS_TO_HTTP para accounts.json...")
            set_socks_proxy("src/accounts.json")

        if CONFIG_MODE != "ZIP":
            config_json_url = f"https://drive.kingvegeta.workers.dev/1:/Files/rewanced/_{CONFIG_MODE}.json"
            print(f"Baixando config.json ({CONFIG_MODE}) de {config_json_url}...")
            curl_with_proxy_fallback(config_json_url, "src/config.json")
            print(f"Atualizando IDCLUSTER em src/config.json para _{BOT_ACCOUNT}...")
            subprocess.run(f"sed -i 's/_IDCLUSTER/_{BOT_ACCOUNT}/g' src/config.json", shell=True, check=True)
        else:
            print("Modo ZIP: Pulando download e modificação do config.json.")

        #print("Executando npm run build...")
        #subprocess.run("npm run build", shell=True, check=True)
        print(f"--- ✅ Bot {bot_id} configurado com sucesso ---")

    except subprocess.CalledProcessError as e:
        print(f"⚠️ ERRO: Falha em um subproceso para Bot {bot_id} no diretório {os.getcwd()}: {e}")
    except FileNotFoundError as e:
        print(f"⚠️ ERRO: Arquivo ou diretório não encontrado para Bot {bot_id}: {e}")
    except Exception as e:
        print(f"⚠️ ERRO inesperado durante a configuração do Bot {bot_id}: {e}")
    finally:
        os.chdir(original_cwd)
        print(f"Diretório de trabalho restaurado para: {os.getcwd()}")

def download_and_extract_bot_B(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE):
    bot_id = "B"
    bot_dir = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_id}")
    print(f"\n--- Iniciando configuração para Bot {bot_id} ---")
    print(f"Diretório alvo: {bot_dir}")

    original_cwd = os.getcwd()
    try:
        if not os.path.isdir(bot_dir):
            print(f"⚠️ ERRO: Diretório {bot_dir} não encontrado. Pulando Bot {bot_id}.")
            return

        os.chdir(bot_dir)
        print(f"Diretório de trabalho alterado para: {os.getcwd()}")

        zip_file_name = BOT_ZIP_FILE_NAME
        download_url = f"{BOT_DIRECTORY}{BOT_ACCOUNT}_{bot_id}.zip"

        print(f"Baixando {download_url} para {zip_file_name}...")
        curl_with_proxy_fallback(download_url, zip_file_name)

        print(f"Extraindo {zip_file_name}...")
        subprocess.run(f"unzip -o {zip_file_name}", shell=True, check=True)

        print(f"Removendo {zip_file_name}...")
        subprocess.run(f"rm -f {zip_file_name}", shell=True, check=True)

        if CONFIG_MODE == "GEN_COOKIE_CONFIG":
            print("Aplicando proxy local para geração de cookies...")
            clean_account_proxys("src/accounts.json")
        
        if CONFIG_MODE == "DEFAULT_CONFIG_US":
            print("Aplicando proxy local para configuração padrão dos EUA...")
            clean_account_proxys("src/accounts.json")

        if SOCKS_PROXY == True:
            print("Ativando proxy SOCKS_TO_HTTP para accounts.json...")
            set_socks_proxy("src/accounts.json")

        if CONFIG_MODE != "ZIP":
            config_json_url = f"https://drive.kingvegeta.workers.dev/1:/Files/rewanced/_{CONFIG_MODE}.json"
            print(f"Baixando config.json ({CONFIG_MODE}) de {config_json_url}...")
            curl_with_proxy_fallback(config_json_url, "src/config.json")
            print(f"Atualizando IDCLUSTER em src/config.json para _{BOT_ACCOUNT}...")
            subprocess.run(f"sed -i 's/_IDCLUSTER/_{BOT_ACCOUNT}/g' src/config.json", shell=True, check=True)
        else:
            print("Modo ZIP: Pulando download e modificação do config.json.")

        #print("Executando npm run build...")
        #subprocess.run("npm run build", shell=True, check=True)
        print(f"--- ✅ Bot {bot_id} configurado com sucesso ---")

    except subprocess.CalledProcessError as e:
        print(f"⚠️ ERRO: Falha em um subproceso para Bot {bot_id} no diretório {os.getcwd()}: {e}")
    except FileNotFoundError as e:
        print(f"⚠️ ERRO: Arquivo ou diretório não encontrado para Bot {bot_id}: {e}")
    except Exception as e:
        print(f"⚠️ ERRO inesperado durante a configuração do Bot {bot_id}: {e}")
    finally:
        os.chdir(original_cwd)
        print(f"Diretório de trabalho restaurado para: {os.getcwd()}")

def download_and_extract_bot_C(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE):
    bot_id = "C"
    bot_dir = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_id}")
    print(f"\n--- Iniciando configuração para Bot {bot_id} ---")
    print(f"Diretório alvo: {bot_dir}")

    original_cwd = os.getcwd()
    try:
        if not os.path.isdir(bot_dir):
            print(f"⚠️ ERRO: Diretório {bot_dir} não encontrado. Pulando Bot {bot_id}.")
            return

        os.chdir(bot_dir)
        print(f"Diretório de trabalho alterado para: {os.getcwd()}")

        zip_file_name = BOT_ZIP_FILE_NAME
        download_url = f"{BOT_DIRECTORY}{BOT_ACCOUNT}_{bot_id}.zip"

        print(f"Baixando {download_url} para {zip_file_name}...")
        curl_with_proxy_fallback(download_url, zip_file_name)

        print(f"Extraindo {zip_file_name}...")
        subprocess.run(f"unzip -o {zip_file_name}", shell=True, check=True)

        print(f"Removendo {zip_file_name}...")
        subprocess.run(f"rm -f {zip_file_name}", shell=True, check=True)

        if CONFIG_MODE == "GEN_COOKIE_CONFIG":
            print("Aplicando proxy local para geração de cookies...")
            clean_account_proxys("src/accounts.json")
        
        if CONFIG_MODE == "DEFAULT_CONFIG_US":
            print("Aplicando proxy local para configuração padrão dos EUA...")
            clean_account_proxys("src/accounts.json")

        if SOCKS_PROXY == True:
            print("Ativando proxy SOCKS_TO_HTTP para accounts.json...")
            set_socks_proxy("src/accounts.json")

        if CONFIG_MODE != "ZIP":
            config_json_url = f"https://drive.kingvegeta.workers.dev/1:/Files/rewanced/_{CONFIG_MODE}.json"
            print(f"Baixando config.json ({CONFIG_MODE}) de {config_json_url}...")
            curl_with_proxy_fallback(config_json_url, "src/config.json")
            print(f"Atualizando IDCLUSTER em src/config.json para _{BOT_ACCOUNT}...")
            subprocess.run(f"sed -i 's/_IDCLUSTER/_{BOT_ACCOUNT}/g' src/config.json", shell=True, check=True)
        else:
            print("Modo ZIP: Pulando download e modificação do config.json.")

        #print("Executando npm run build...")
        #subprocess.run("npm run build", shell=True, check=True)
        print(f"--- ✅ Bot {bot_id} configurado com sucesso ---")

    except subprocess.CalledProcessError as e:
        print(f"⚠️ ERRO: Falha em um subproceso para Bot {bot_id} no diretório {os.getcwd()}: {e}")
    except FileNotFoundError as e:
        print(f"⚠️ ERRO: Arquivo ou diretório não encontrado para Bot {bot_id}: {e}")
    except Exception as e:
        print(f"⚠️ ERRO inesperado durante a configuração do Bot {bot_id}: {e}")
    finally:
        os.chdir(original_cwd)
        print(f"Diretório de trabalho restaurado para: {os.getcwd()}")

def download_and_extract_bot_D(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE):
    bot_id = "D"
    bot_dir = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_id}")
    print(f"\n--- Iniciando configuração para Bot {bot_id} ---")
    print(f"Diretório alvo: {bot_dir}")

    original_cwd = os.getcwd()
    try:
        if not os.path.isdir(bot_dir):
            print(f"⚠️ ERRO: Diretório {bot_dir} não encontrado. Pulando Bot {bot_id}.")
            return

        os.chdir(bot_dir)
        print(f"Diretório de trabalho alterado para: {os.getcwd()}")

        zip_file_name = BOT_ZIP_FILE_NAME
        download_url = f"{BOT_DIRECTORY}{BOT_ACCOUNT}_{bot_id}.zip"

        print(f"Baixando {download_url} para {zip_file_name}...")
        curl_with_proxy_fallback(download_url, zip_file_name)

        print(f"Extraindo {zip_file_name}...")
        subprocess.run(f"unzip -o {zip_file_name}", shell=True, check=True)

        print(f"Removendo {zip_file_name}...")
        subprocess.run(f"rm -f {zip_file_name}", shell=True, check=True)

        if CONFIG_MODE == "GEN_COOKIE_CONFIG":
            print("Aplicando proxy local para geração de cookies...")
            clean_account_proxys("src/accounts.json")
        
        if CONFIG_MODE == "DEFAULT_CONFIG_US":
            print("Aplicando proxy local para configuração padrão dos EUA...")
            clean_account_proxys("src/accounts.json")

        if SOCKS_PROXY == True:
            print("Ativando proxy SOCKS_TO_HTTP para accounts.json...")
            set_socks_proxy("src/accounts.json")

        if CONFIG_MODE != "ZIP":
            config_json_url = f"https://drive.kingvegeta.workers.dev/1:/Files/rewanced/_{CONFIG_MODE}.json"
            print(f"Baixando config.json ({CONFIG_MODE}) de {config_json_url}...")
            curl_with_proxy_fallback(config_json_url, "src/config.json")
            print(f"Atualizando IDCLUSTER em src/config.json para _{BOT_ACCOUNT}...")
            subprocess.run(f"sed -i 's/_IDCLUSTER/_{BOT_ACCOUNT}/g' src/config.json", shell=True, check=True)
        else:
            print("Modo ZIP: Pulando download e modificação do config.json.")

        #print("Executando npm run build...")
        #subprocess.run("npm run build", shell=True, check=True)
        print(f"--- ✅ Bot {bot_id} configurado com sucesso ---")

    except subprocess.CalledProcessError as e:
        print(f"⚠️ ERRO: Falha em um subproceso para Bot {bot_id} no diretório {os.getcwd()}: {e}")
    except FileNotFoundError as e:
        print(f"⚠️ ERRO: Arquivo ou diretório não encontrado para Bot {bot_id}: {e}")
    except Exception as e:
        print(f"⚠️ ERRO inesperado durante a configuração do Bot {bot_id}: {e}")
    finally:
        os.chdir(original_cwd)
        print(f"Diretório de trabalho restaurado para: {os.getcwd()}")

def download_and_extract_bot_E(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE):
    bot_id = "E"
    bot_dir = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{bot_id}")
    print(f"\n--- Iniciando configuração para Bot {bot_id} ---")
    print(f"Diretório alvo: {bot_dir}")

    original_cwd = os.getcwd()
    try:
        if not os.path.isdir(bot_dir):
            print(f"⚠️ ERRO: Diretório {bot_dir} não encontrado. Pulando Bot {bot_id}.")
            return

        os.chdir(bot_dir)
        print(f"Diretório de trabalho alterado para: {os.getcwd()}")

        zip_file_name = BOT_ZIP_FILE_NAME
        download_url = f"{BOT_DIRECTORY}{BOT_ACCOUNT}_{bot_id}.zip"

        print(f"Baixando {download_url} para {zip_file_name}...")
        curl_with_proxy_fallback(download_url, zip_file_name)

        print(f"Extraindo {zip_file_name}...")
        subprocess.run(f"unzip -o {zip_file_name}", shell=True, check=True)

        print(f"Removendo {zip_file_name}...")
        subprocess.run(f"rm -f {zip_file_name}", shell=True, check=True)

        if CONFIG_MODE == "GEN_COOKIE_CONFIG":
            print("Aplicando proxy local para geração de cookies...")
            clean_account_proxys("src/accounts.json")
        
        if CONFIG_MODE == "DEFAULT_CONFIG_US":
            print("Aplicando proxy local para configuração padrão dos EUA...")
            clean_account_proxys("src/accounts.json")

        if SOCKS_PROXY == True:
            print("Ativando proxy SOCKS_TO_HTTP para accounts.json...")
            set_socks_proxy("src/accounts.json")

        if CONFIG_MODE != "ZIP":
            config_json_url = f"https://drive.kingvegeta.workers.dev/1:/Files/rewanced/_{CONFIG_MODE}.json"
            print(f"Baixando config.json ({CONFIG_MODE}) de {config_json_url}...")
            curl_with_proxy_fallback(config_json_url, "src/config.json")
            print(f"Atualizando IDCLUSTER em src/config.json para _{BOT_ACCOUNT}...")
            subprocess.run(f"sed -i 's/_IDCLUSTER/_{BOT_ACCOUNT}/g' src/config.json", shell=True, check=True)
        else:
            print("Modo ZIP: Pulando download e modificação do config.json.")

        #print("Executando npm run build...")
        #subprocess.run("npm run build", shell=True, check=True)
        print(f"--- ✅ Bot {bot_id} configurado com sucesso ---")

    except subprocess.CalledProcessError as e:
        print(f"⚠️ ERRO: Falha em um subproceso para Bot {bot_id} no diretório {os.getcwd()}: {e}")
    except FileNotFoundError as e:
        print(f"⚠️ ERRO: Arquivo ou diretório não encontrado para Bot {bot_id}: {e}")
    except Exception as e:
        print(f"⚠️ ERRO inesperado durante a configuração do Bot {bot_id}: {e}")
    finally:
        os.chdir(original_cwd)
        print(f"Diretório de trabalho restaurado para: {os.getcwd()}")

def mount_rewards_drive():
    """Monta o drive de recompensas e lista as sessões"""
    subprocess.run("sleep 2", shell=True)
    for letter in ['A', 'B', 'C', 'D', 'E']:
        subprocess.run(f"umount -l \"{BASEDIR}/{BOT_BASE_DIR_NAME}_{letter}/dist/browser/sessions\"", shell=True)
    
    time.sleep(3)

    # Inicialmente monta todos
    for letter in ['A', 'B', 'C', 'D', 'E']:
        subprocess.run(f"nohup ricronus --config {BASEDIR}/ricronus.conf mount rewards:Rewards \"{BASEDIR}/{BOT_BASE_DIR_NAME}_{letter}/dist/browser/sessions\" &> /dev/null 2>&1 &", shell=True)
    
    mount_points = [f"{BASEDIR}/{BOT_BASE_DIR_NAME}_{letter}/dist/browser/sessions" for letter in ['A', 'B', 'C', 'D', 'E']]
    max_attempts = 3
    retry_delay = 3  # segundos
    
    for attempt in range(1, max_attempts + 1):
        print(f"🔄 Verificando montagens (tentativa {attempt}/{max_attempts})...")
        failed_mounts = []
        
        for mount_point in mount_points:
            time.sleep(2)  # Dá tempo para o mount acontecer
            if os.path.isdir(mount_point) and os.listdir(mount_point):
                print(f"✅ {mount_point} montado corretamente.")
            else:
                print(f"⚠️ {mount_point} não montado ou vazio. Re-montando...")
                failed_mounts.append(mount_point)
        
        if not failed_mounts:
            print("✅ Todas as montagens concluídas com sucesso!")
            break
        
        # Tenta remontar os que falharam
        for mount_point in failed_mounts:
            subprocess.run(f"umount -l \"{mount_point}\"", shell=True)
            subprocess.run(f"nohup ricronus --config {BASEDIR}/ricronus.conf mount rewards:Rewards \"{mount_point}\" &> /dev/null 2>&1 &", shell=True)
        
        if attempt < max_attempts:
            print(f"⏳ Aguardando {retry_delay} segundos antes de nova tentativa...")
            time.sleep(retry_delay)
    else:
        print("❌ Algumas montagens falharam após várias tentativas.")
    
def copy_rewards_drive(BOT_ACCOUNT):
    target = f"{BASEDIR}/{BOT_BASE_DIR_NAME}_shared/sessions/_{BOT_ACCOUNT}"

    print(f"🚀 Iniciando cópia de rewards:Rewards/_\"{BOT_ACCOUNT}\" para {target}...")
    try:
        result = subprocess.run(
            f"ricronus --config {BASEDIR}/ricronus.conf copy rewards:Rewards/_\"{BOT_ACCOUNT}\" \"{target}\" --transfers 10 --fast-list",
            shell=True,
            check=True,
            capture_output=True,
            text=True
        )
        print("Cópia concluída com sucesso.")
    except subprocess.CalledProcessError as e:
        # Se o erro for porque a pasta não existe na nuvem, cria localmente
        if "directory not found" in (e.stderr or "").lower() or "not found" in (e.stderr or "").lower():
            print(f"⚠️ Pasta rewards:Rewards/_{BOT_ACCOUNT} não existe na nuvem. Criando localmente {target} ...")
            os.makedirs(target, exist_ok=True)
        else:
            print(f"⚠️ Erro ao copiar rewards:Rewards para {target}: {e}\nSaída: {e.output}\nErro: {e.stderr}")

    for letter in ['A', 'B', 'C', 'D', 'E']:
        symlink_path = f"{BASEDIR}/{BOT_BASE_DIR_NAME}_{letter}/dist/browser/sessions/_{BOT_ACCOUNT}"
        os.makedirs(os.path.dirname(symlink_path), exist_ok=True)
        
        # Remove o caminho anterior se já existir
        if os.path.islink(symlink_path):
            os.unlink(symlink_path)
        elif os.path.isdir(symlink_path):
            shutil.rmtree(symlink_path)
        elif os.path.exists(symlink_path):
            os.remove(symlink_path)

        os.symlink(target, symlink_path)
        print(f"🔗 Link simbólico criado: {symlink_path} ➝ {target}")

def upload_rewards_drive(BOT_ACCOUNT):
    target = f"{BASEDIR}/{BOT_BASE_DIR_NAME}_shared/sessions/_{BOT_ACCOUNT}"

    print(f"🚀 Iniciando upload {target} rewards:Rewards/_{BOT_ACCOUNT} ...")
    subprocess.run(
        f"ricronus --config {BASEDIR}/ricronus.conf copy \"{target}\" rewards:Rewards/_{BOT_ACCOUNT} --transfers 10 --fast-list --update",
        shell=True
    )
    print(f"Upload concluido.")

def execute_tasks_for_selected_bots(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE, *selected_bots):
    if CONFIG_MODE == "ZIP":
        print(f"📦 Modo CONFIG ZIP detectado!")
    if "A" in selected_bots:
        download_and_extract_bot_A(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE)
    if "B" in selected_bots:
        download_and_extract_bot_B(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE)
    if "C" in selected_bots:
        download_and_extract_bot_C(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE)
    if "D" in selected_bots:
        download_and_extract_bot_D(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE)
    if "E" in selected_bots:
        download_and_extract_bot_E(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE)

def run_command(command, prefix="", timeout=3600):
    """
    Executa um comando no shell e exibe a saída em tempo real.
    Inclui timeout para evitar travamentos e melhor tratamento de erros.
    
    Args:
        command: Comando a ser executado
        prefix: Prefixo para as mensagens de saída
        timeout: Tempo máximo de execução em segundos (padrão: 1 hora)
    """
    try:
        # Usar subprocess com timeout em vez de sinais
        process = subprocess.Popen(
            command, 
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Função para ler e imprimir saída de um pipe
        def read_pipe(pipe, error_stream=False):
            prefix_symbol = "❌" if error_stream else "ℹ️"
            for line in iter(pipe.readline, ''):
                if line:
                    print(f"{prefix} {prefix_symbol}: {line}", end='', flush=True)
        
        # Criar threads para ler stdout e stderr simultaneamente
        stdout_thread = threading.Thread(target=read_pipe, args=(process.stdout,))
        stderr_thread = threading.Thread(target=read_pipe, args=(process.stderr, True))
        
        stdout_thread.daemon = True
        stderr_thread.daemon = True
        
        stdout_thread.start()
        stderr_thread.start()
        
        # Usar um loop com verificação de tempo em vez de wait() com timeout
        start_time = time.time()
        while process.poll() is None:
            # Verificar se excedeu o timeout
            if time.time() - start_time > timeout:
                process.kill()
                print(f"{prefix} ⏱️: Comando excedeu o tempo limite de {timeout} segundos")
                return False
            time.sleep(0.5)  # Pequena pausa para não sobrecarregar a CPU
        
        # Aguardar as threads terminarem (com timeout)
        stdout_thread.join(5)
        stderr_thread.join(5)
        
        # Verificar código de saída
        exit_code = process.returncode
        if exit_code != 0:
            print(f"{prefix} ❌: Comando falhou com código de saída {exit_code}")
            return False
        
        return True
        
    except Exception as e:
        print(f"{prefix} ❌: Erro ao executar comando: {str(e)}")
        # Tentar matar o processo se ele ainda estiver em execução
        try:
            process.kill()
        except:
            pass
        return False

def start_bots(discord_webhook_url_br, discord_webhook_url_us, *bots_to_run):
    """
    Executa BOTs específicos com delay progressivo entre eles.
    Exemplo de uso: start_bots('A', 'B', 'D') para executar apenas os bots A, B e D.
    Se nenhum bot for especificado, executa os bots A e B por padrão.
    Args:
        discord_webhook_url_br: URL do webhook do Discord para BR.
        discord_webhook_url_us: URL do webhook do Discord para US.
        *bots_to_run: Lista de letras dos bots a serem executados.
    """
    global is_shutdown_requested, banned_bots  # Declarar uso da variável global
    
    # Shutdown flag
    is_shutdown_requested = False
    if not bots_to_run:
        bots_to_run = ['A', 'B']

    # Converte para maiúsculas para garantir consistência
    bots_to_run = [bot.upper() for bot in bots_to_run]
    
    # Verificar status de bots banidos
    if banned_bots:
        banned_in_request = [bot for bot in bots_to_run if bot in banned_bots]
        if banned_in_request:
            print(f"⚠️ Aviso: Os seguintes bots estão na lista de banidos e NÃO serão iniciados: {', '.join(banned_in_request)}")
            # Filtrar bots banidos da lista de execução
            bots_to_run = [bot for bot in bots_to_run if bot not in banned_bots]
            if not bots_to_run:
                print("❌ Todos os bots solicitados estão banidos. Nenhum bot será iniciado.")
                return
        
        all_banned = ", ".join(sorted(banned_bots))
        print(f"🚫 Bots atualmente banidos: {all_banned}")
    else:
        print("✅ Nenhum bot está atualmente banido.")
    
    if bots_to_run:
        active_bots = ", ".join(bots_to_run)
        print(f"🚀 Bots que serão iniciados: {active_bots}")
    
    # Dicionário com os comandos para cada bot
    commands = {
        'A': f"cd {BASEDIR}/{BOT_BASE_DIR_NAME}_A && TZ=America/Sao_Paulo npm run start",
        'B': f"cd {BASEDIR}/{BOT_BASE_DIR_NAME}_B && TZ=America/Sao_Paulo npm run start",
        'C': f"cd {BASEDIR}/{BOT_BASE_DIR_NAME}_C && TZ=America/Sao_Paulo npm run start",
        'D': f"cd {BASEDIR}/{BOT_BASE_DIR_NAME}_D && TZ=America/Sao_Paulo npm run start",
        'E': f"cd {BASEDIR}/{BOT_BASE_DIR_NAME}_E && TZ=America/Sao_Paulo npm run start",
    }
    
    # Cores ANSI para cada bot
    bot_colors = {
        'A': '\033[92m',  # Verde
        'B': '\033[94m',  # Azul
        'C': '\033[93m',  # Amarelo
        'D': '\033[95m',  # Magenta
        'E': '\033[96m',  # Ciano
        'Sistema': '\033[97m',  # Branco
        'Erro': '\033[91m',  # Vermelho para erros
        'Aviso': '\033[33m',  # Laranja para avisos
        'Sucesso': '\033[32m'  # Verde escuro para sucesso
    }
    
    # Código ANSI para resetar a cor
    reset_color = '\033[0m'
    
    # Função para imprimir com cor
    def print_colored(bot, message, is_error=False, is_warning=False, is_success=False):
        if is_error:
            color = bot_colors.get('Erro', reset_color)
        elif is_warning:
            color = bot_colors.get('Aviso', reset_color)
        elif is_success:
            color = bot_colors.get('Sucesso', reset_color)
        else:
            color = bot_colors.get(bot, reset_color)
        # Usar sys.stdout.write para garantir que vá para o logger redirecionado
        # e flush para tentar forçar a escrita imediata.
        sys.stdout.write(f"{color}[{bot}]: {message}{reset_color}\n")
        sys.stdout.flush()
    
    # Lista para armazenar os processos
    processes = {}
    
    # Contador de reinicializações para cada bot
    restart_counts = {bot: 0 for bot in bots_to_run}
    max_restarts = 8  # Número máximo de erros críticos antes de parar de reiniciar
    
    # Controle de estado dos bots (novo)
    bot_states = {bot: 'running' for bot in bots_to_run}  # 'running', 'completed', 'failed', 'banned', 'inactive_timeout'
    
    # Contador de reinicializações por timeout de inatividade para cada bot
    timeout_restart_counts = {bot: 0 for bot in bots_to_run}
    max_timeout_restarts = 1  # Número máximo de tentativas de reinício após timeout por inatividade
    
    # Controle de tempo de última atividade para cada bot
    bot_last_activity = {bot: time.time() for bot in bots_to_run}
    
    # Controle da última mensagem de atividade para cada bot
    bot_last_message = {bot: "Bot iniciado" for bot in bots_to_run}
    
    # Timeout de inatividade (30 minutos = 1800 segundos)
    INACTIVITY_TIMEOUT = 30 * 60  # 30 minutos
    
    # Padrões de erro críticos que causam o fechamento do bot
    critical_error_patterns = [
        "Error: EIO: i/o error, close",
        "[MAIN-ERROR] Error running desktop bot: undefined",
        "ECONNRESET",
        "ERR_UNHANDLED_REJECTION",
        "ENOTCONN:",
        "Navigation timeout of",
        "[LOGIN] An error occurred: TimeoutError",
        "Error running desktop bot",
        "Too Many Requests",
        "Terminating bot due to",
        "Email field not present",
        #"[LOGIN] Email field not found",
        "Error: SyntaxError"
    ]
    
    # Função para iniciar um bot com delay
    def start_delayed_bot(bot_letter, position, is_restart=False):
        try:
            # Verificar se o bot está na lista de banidos antes de iniciar
            if bot_letter in banned_bots:
                print_colored('Sistema', f"Bot {bot_letter} está na lista de contas banidas. Não será iniciado.", is_error=True)
                return False
            
            # Se for uma reinicialização, não aplicar o delay inicial
            if not is_restart:
                # Delay progressivo: 0 seg para o primeiro, BOT_START_DELAY_SECONDS para o segundo, etc.
                delay = position * BOT_START_DELAY_SECONDS  # Delay configurável multiplicado pela posição

                if delay > 0:
                    print_colored('Sistema', f"Bot {bot_letter} iniciará em {delay} segundos...")
                    time.sleep(delay)
            
            # Mensagem diferente para reinicialização
            if is_restart:
                print_colored('Sistema', f"Reiniciando Bot {bot_letter} após erro crítico...", is_warning=True)
            else:
                print_colored('Sistema', f"Iniciando Bot {bot_letter} agora...")
            
            # Verificações de pré-requisitos
            bot_dir = f"{BASEDIR}/{BOT_BASE_DIR_NAME}_{bot_letter}"
            if not os.path.exists(bot_dir):
                print_colored('Sistema', f"Diretório do Bot {bot_letter} não encontrado: {bot_dir}", is_error=True)
                return False
                
            if not os.path.exists(f"{bot_dir}/package.json"):
                print_colored('Sistema', f"package.json não encontrado para Bot {bot_letter}", is_error=True)
                return False
                
            if not os.path.exists(f"{bot_dir}/dist"):
                print_colored('Sistema', f"Diretório dist não encontrado para Bot {bot_letter}. A compilação pode ter falhado.", is_error=True)
                return False
            
            # Comando para executar o bot
            command = f"""
            cd {bot_dir} && 
            echo "Verificando ambiente do Bot {bot_letter}..." &&
            echo "Node version: $(node -v)" &&
            echo "NPM version: $(npm -v)" &&
            echo "Iniciando execução do Bot {bot_letter}..." &&
            TZ=America/Sao_Paulo npm run start 2>&1
            """
            
            # Iniciar o processo
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            with processes_lock:
                processes[bot_letter] = process
            
            # Função para monitorar a saída do processo
            def monitor_output(process, bot_letter):
                try:
                    no_output_counter = 0
                    start_time = time.time()
                    last_critical_error = None  # Armazenar o último erro crítico detectado
                    
                    # Registrar o PID do processo principal
                    if process.pid:
                        if bot_letter in bot_pids:
                            bot_pids[bot_letter].append(process.pid)
                            print_colored('Sistema', f"PID principal {process.pid} registrado para Bot {bot_letter}", is_success=True)
                    
                    # Ler a saída linha por linha
                    for line in iter(process.stdout.readline, ''):
                        if line.strip():  # Ignorar linhas vazias
                            # Atualizar timestamp da última atividade
                            bot_last_activity[bot_letter] = time.time()
                            
                            # Capturar e limpar a última mensagem para armazenar
                            cleaned_line = line.strip()
                            # Remover códigos de cores ANSI e caracteres especiais
                            cleaned_line = re.sub(r'\x1b\[[0-9;]*m', '', cleaned_line)
                            # Limitar o tamanho da mensagem para evitar overflow no Discord
                            if len(cleaned_line) > 100:
                                cleaned_line = cleaned_line[:97] + "..."
                            bot_last_message[bot_letter] = cleaned_line
                            
                            # Extrair PIDs da saída
                            if "[PID:" in line or "PID:" in line or "pid:" in line:
                                try:
                                    # Extrair o PID usando expressão regular
                                    pid_match = re.search(r'PID:?\s*(\d+)', line, re.IGNORECASE)
                                    if pid_match:
                                        pid = int(pid_match.group(1))
                                        if pid not in bot_pids[bot_letter]:
                                            bot_pids[bot_letter].append(pid)
                                            print_colored('Sistema', f"PID {pid} registrado para Bot {bot_letter}", is_success=True)
                                except:
                                    pass
                            
                            # Na função monitor_output, dentro do loop que processa a saída do bot:
                            # Verificar se a linha contém informações sobre pontos e adicionar emotes se necessário
                            for key in ["Current total:", "Current point count:"]:
                                if key in line:
                                    try:
                                        total_text = line.split(key)[1].strip()
                                        total_points = int(''.join(filter(str.isdigit, total_text)))
                                        if total_points > 1:
                                            original_line = line.strip()
                                            line = f"🚨🚨🚨 {original_line} 🚨🚨🚨"
                                            threading.Thread(target=send_discord_redeem_alert, args=(bot_letter, original_line, discord_webhook_url_br, discord_webhook_url_us)).start()
                                    except (ValueError, IndexError):
                                        pass
                                    break  # Garante que só processa uma vez por linha
                            if "Account has been suspended!" in line:
                                bot_states[bot_letter] = 'banned'  # Marcar como banido
                                threading.Thread(target=send_discord_suspension_alert, args=(bot_letter, discord_webhook_url_br, discord_webhook_url_us)).start()
                            
                            # Verificar erros que requerem deleção de cookies
                            if "Invalid cookie fields" in line or "net::ERR_TUNNEL_CONNECTION_FAILED" in line:
                                error_type = "cookies inválidos" if "Invalid cookie fields" in line else "erro de conexão tunnel"
                                print_colored('Sistema', f"Erro de {error_type} detectado no Bot {bot_letter}. Deletando cookies...", is_warning=True)
                                if delete_bot_cookies(bot_letter):
                                    print_colored('Sistema', f"Cookies do Bot {bot_letter} deletados com sucesso.", is_success=True)
                                else:
                                    print_colored('Sistema', f"Falha ao deletar cookies do Bot {bot_letter}.", is_error=True)
                            
                            print_colored(bot_letter, line.strip())
                            no_output_counter = 0
                            
                            # Verificar se a linha contém algum dos padrões de erro crítico
                            critical_error_found = None
                            for pattern in critical_error_patterns:
                                if pattern in line:
                                    critical_error_found = pattern
                                    last_critical_error = line.strip()  # Capturar a linha completa do erro
                                    break
                            
                            if critical_error_found:
                                print_colored('Sistema', f"Detectado erro crítico no Bot {bot_letter}: {critical_error_found}", is_error=True)
                                
                                # Verificar se o bot está na lista de banidos
                                if bot_letter in banned_bots:
                                    print_colored('Sistema', f"Bot {bot_letter} está na lista de contas banidas. Não será reiniciado.", is_error=True)
                                    return
                                
                                # Verificar se não está em processo de desligamento antes de tentar reiniciar
                                if not is_shutdown_requested:
                                    if restart_counts[bot_letter] < max_restarts:
                                        time.sleep(10)
                                        restart_counts[bot_letter] += 1
                                        print_colored('Sistema', f"Tentativa de reinicialização {restart_counts[bot_letter]}/{max_restarts} para Bot {bot_letter}", is_warning=True)
                                        
                                        # Enviar mensagem para Discord com detalhes do erro
                                        DISCORD_WEBHOOK_LOG = discord_webhook_log_env
                                        BOT_ACC = bot_acc_env
                                        # Limpar a mensagem de erro antes de enviar
                                        cleaned_error = clean_error_message(last_critical_error)
                                        error_message = f"Reiniciando Bot {bot_letter} após erro crítico: {cleaned_error}"
                                        send_discord_log_message(BOT_ACC, error_message, DISCORD_WEBHOOK_LOG)
                                        
                                        # Encerrar o processo atual
                                        process.terminate()
                                        try:
                                            process.wait(timeout=10)
                                        except subprocess.TimeoutExpired:
                                            process.kill()
                                        
                                        # Remover o processo antigo do dicionário
                                        with processes_lock:
                                            if bot_letter in processes:
                                                del processes[bot_letter]
                                        
                                        # Iniciar uma nova thread para reiniciar o bot após um breve delay
                                        def restart_bot_wrapper():
                                            time.sleep(10)
                                            new_process = start_delayed_bot(bot_letter, position, is_restart=True)
                                            if new_process:
                                                # Adicionar o novo processo ao dicionário global
                                                with processes_lock:
                                                    processes[bot_letter] = new_process
                                                print_colored('Sistema', f"Bot {bot_letter} reiniciado com sucesso.", is_success=True)
                                            else:
                                                print_colored('Sistema', f"Falha ao reiniciar Bot {bot_letter}.", is_error=True)
                                        
                                        restart_thread = threading.Thread(target=restart_bot_wrapper)
                                        restart_thread.daemon = False  # Não daemon para não morrer com o programa principal
                                        restart_thread.start()
                                        return
                                    else:
                                        print_colored('Sistema', f"Número máximo de reinicializações ({max_restarts}) atingido para Bot {bot_letter}. Não será reiniciado.", is_error=True)
                                        bot_states[bot_letter] = 'failed'  # Marcar como falhou definitivamente
                                        # Enviar notificação para Discord sobre max restarts atingido
                                        last_err = last_critical_error if last_critical_error else "Erro crítico não especificado"
                                        threading.Thread(target=send_discord_max_restart_alert, args=(bot_letter, discord_webhook_url_br, discord_webhook_url_us, max_restarts, last_err)).start()
                                else:
                                    print_colored('Sistema', f"Desligamento solicitado. Bot {bot_letter} não será reiniciado.", is_warning=True)

                        else:
                            no_output_counter += 1
                            
                            # Verificar timeout de inatividade quando não há saída
                            current_time = time.time()
                            time_since_last_activity = current_time - bot_last_activity[bot_letter]
                            
                            if time_since_last_activity > INACTIVITY_TIMEOUT:
                                print_colored('Sistema', f"Bot {bot_letter} ficou inativo por {int(time_since_last_activity/60)} minutos. Encerrando por timeout de inatividade.", is_warning=True)
                                
                                # Verificar se já tentou reiniciar por timeout
                                if timeout_restart_counts[bot_letter] < max_timeout_restarts:
                                    # Ainda pode tentar reiniciar
                                    timeout_restart_counts[bot_letter] += 1
                                    print_colored('Sistema', f"Bot {bot_letter} inativo - tentando reiniciar ({timeout_restart_counts[bot_letter]}/{max_timeout_restarts})...", is_warning=True)
                                    
                                    # Enviar mensagem para Discord sobre reinício por inatividade
                                    last_msg = bot_last_message.get(bot_letter, "Nenhuma atividade recente")
                                    
                                    # Encerrar o processo atual
                                    try:
                                        process.terminate()
                                        time.sleep(5)
                                        if process.poll() is None:
                                            process.kill()
                                    except Exception as e:
                                        print_colored('Sistema', f"Erro ao encerrar Bot {bot_letter}: {str(e)}", is_error=True)
                                    
                                    # Remover o processo antigo do dicionário
                                    with processes_lock:
                                        if bot_letter in processes:
                                            del processes[bot_letter]
                                    
                                    # Resetar o timestamp de última atividade
                                    bot_last_activity[bot_letter] = time.time()
                                    bot_last_message[bot_letter] = "Bot reiniciado após timeout de inatividade"
                                    
                                    # Iniciar uma nova thread para reiniciar o bot
                                    def restart_bot_timeout_wrapper():
                                        time.sleep(10)
                                        new_process = start_delayed_bot(bot_letter, 0, is_restart=True)
                                        if new_process:
                                            with processes_lock:
                                                processes[bot_letter] = new_process
                                            bot_states[bot_letter] = 'running'
                                            print_colored('Sistema', f"Bot {bot_letter} reiniciado com sucesso após timeout de inatividade.", is_success=True)
                                        else:
                                            print_colored('Sistema', f"Falha ao reiniciar Bot {bot_letter} após timeout.", is_error=True)
                                            bot_states[bot_letter] = 'inactive_timeout'
                                    
                                    restart_thread = threading.Thread(target=restart_bot_timeout_wrapper)
                                    restart_thread.daemon = False
                                    restart_thread.start()
                                    return
                                else:
                                    # Já tentou reiniciar, agora encerra definitivamente
                                    print_colored('Sistema', f"Bot {bot_letter} já foi reiniciado {max_timeout_restarts}x por inatividade. Encerrando definitivamente.", is_warning=True)
                                    
                                    # Marcar como encerrado por inatividade
                                    bot_states[bot_letter] = 'inactive_timeout'
                                    
                                    # Enviar mensagem para Discord sobre o encerramento por inatividade
                                    last_msg = bot_last_message.get(bot_letter, "Nenhuma atividade recente")
                                    threading.Thread(target=send_discord_timeout_alert, args=(bot_letter, discord_webhook_url_br, discord_webhook_url_us, last_msg)).start()
                                    
                                    # Encerrar o processo
                                    try:
                                        process.terminate()
                                        time.sleep(5)
                                        if process.poll() is None:
                                            process.kill()
                                        print_colored('Sistema', f"Bot {bot_letter} encerrado definitivamente por timeout de inatividade.", is_warning=True)
                                    except Exception as e:
                                        print_colored('Sistema', f"Erro ao encerrar Bot {bot_letter}: {str(e)}", is_error=True)
                                    
                                    return  # Encerrar o monitoramento deste bot
                            
                        # Verificar se o processo está sem saída por muito tempo
                        if no_output_counter > 100:
                            if process.poll() is not None:
                                break
                            
                            # Verificar se passou muito tempo sem saída (5 minutos)
                            if time.time() - start_time > 300:
                                print_colored(bot_letter, "Sem saída por 5 minutos, verificando status...", is_warning=True)
                                try:
                                    os.kill(process.pid, 0)  # Verifica se o processo existe
                                    print_colored(bot_letter, "Processo ainda está em execução, continuando...", is_warning=True)
                                except OSError:
                                    print_colored(bot_letter, "Processo não está mais respondendo", is_error=True)
                                    break
                                
                                no_output_counter = 0
                                start_time = time.time()
                    
                    # Verificar o código de saída quando o processo terminar
                    exit_code = process.wait()
                    if exit_code == 0:
                        print_colored('Sistema', f"Bot {bot_letter} concluído com sucesso.", is_success=True)
                        bot_states[bot_letter] = 'completed'  # Marcar como concluído com sucesso
                        
                        # Verificar quais bots ainda estão em execução
                        running_bots = [b for b, p in processes.items() if p.poll() is None and b != bot_letter]
                        if running_bots:
                            running_bots_str = ", ".join(running_bots)
                            print_colored('Sistema', f"Bots {running_bots_str} ainda em execução.", is_warning=True)
                        else:
                            print_colored('Sistema', "Todos os bots concluíram a execução.", is_success=True)
                    else:
                        print_colored('Sistema', f"Bot {bot_letter} encerrou com código {exit_code}.", is_error=True)
                        
                        # Verificar quais bots ainda estão em execução
                        running_bots = [b for b, p in processes.items() if p.poll() is None and b != bot_letter]
                        if running_bots:
                            running_bots_str = ", ".join(running_bots)
                            print_colored('Sistema', f"Bots {running_bots_str} ainda em execução.", is_warning=True)
                        
                        # Tentar reiniciar se o bot encerrou com erro
                        if restart_counts[bot_letter] < max_restarts:
                            # Verificar se o bot está na lista de banidos antes de reiniciar
                            if bot_letter in banned_bots:
                                print_colored('Sistema', f"Bot {bot_letter} está na lista de contas banidas. Não será reiniciado.", is_error=True)
                                bot_states[bot_letter] = 'banned'
                                return
                            
                            restart_counts[bot_letter] += 1
                            print_colored('Sistema', f"Tentativa de reinicialização {restart_counts[bot_letter]}/{max_restarts} para Bot {bot_letter} devido a código de saída {exit_code}", is_warning=True)
                            
                            # Remover o processo antigo do dicionário
                            with processes_lock:
                                if bot_letter in processes:
                                    del processes[bot_letter]
                            
                            # Iniciar uma nova thread para reiniciar o bot após um breve delay
                            def restart_bot_wrapper():
                                time.sleep(10)
                                new_process = start_delayed_bot(bot_letter, position, is_restart=True)
                                if new_process:
                                    # Adicionar o novo processo ao dicionário global
                                    with processes_lock:
                                        processes[bot_letter] = new_process
                                    print_colored('Sistema', f"Bot {bot_letter} reiniciado com sucesso após código de saída {exit_code}.", is_success=True)
                                else:
                                    print_colored('Sistema', f"Falha ao reiniciar Bot {bot_letter} após código de saída {exit_code}.", is_error=True)
                            
                            restart_thread = threading.Thread(target=restart_bot_wrapper)
                            restart_thread.daemon = False  # Não daemon para não morrer com o programa principal
                            restart_thread.start()
                        elif restart_counts[bot_letter] >= max_restarts:
                            # Só enviar notificação se ainda não foi marcado como 'failed' (evita duplicação)
                            if bot_states.get(bot_letter) != 'failed':
                                print_colored('Sistema', f"Número máximo de reinicializações ({max_restarts}) atingido para Bot {bot_letter}. Não será reiniciado.", is_error=True)
                                bot_states[bot_letter] = 'failed'  # Marcar como falhou definitivamente
                                # Enviar notificação para Discord sobre max restarts atingido
                                last_err = f"Código de saída: {exit_code}"
                                threading.Thread(target=send_discord_max_restart_alert, args=(bot_letter, discord_webhook_url_br, discord_webhook_url_us, max_restarts, last_err)).start()
                        
                except Exception as e:
                    print_colored('Sistema', f"Erro ao monitorar Bot {bot_letter}: {str(e)}", is_error=True)
            
            # Iniciar thread para monitorar a saída
            monitor_thread = threading.Thread(target=monitor_output, args=(process, bot_letter))
            monitor_thread.daemon = True
            monitor_thread.start()
            
            # Verificar se o processo iniciou corretamente
            time.sleep(5)
            if process.poll() is not None:
                print_colored('Sistema', f"Bot {bot_letter} encerrou prematuramente com código {process.returncode}", is_error=True)
                # Remover do dicionário de processos se falhou
                with processes_lock:
                    if bot_letter in processes:
                        del processes[bot_letter]
                return None
                
            return process  # Retornar o processo em vez de True
            
        except Exception as e:
            print_colored('Sistema', f"Erro ao iniciar Bot {bot_letter}: {str(e)}", is_error=True)
            return None
    
    # Resto da função permanece igual
    threads = []
    for i, bot_letter in enumerate(bots_to_run):
        if bot_letter in commands:
            def start_initial_bot(bot_letter, position):
                new_process = start_delayed_bot(bot_letter, position, is_restart=False)
                if new_process:
                    print_colored('Sistema', f"Bot {bot_letter} iniciado com sucesso.", is_success=True)
                else:
                    print_colored('Sistema', f"Falha ao iniciar Bot {bot_letter}.", is_error=True)
            
            bot_thread = threading.Thread(target=start_initial_bot, args=(bot_letter, i))
            bot_thread.daemon = False  # Não daemon para não morrer com o programa principal
            bot_thread.start()
            threads.append(bot_thread)
        else:
            print_colored('Sistema', f"Bot {bot_letter} não está configurado.")
    
    # Aguardar um pouco para garantir que os processos iniciem
    time.sleep(10)
    
    # Verificar se algum processo já terminou prematuramente
    for bot_letter, process in list(processes.items()):
        if process.poll() is not None:
            print_colored('Sistema', f"Bot {bot_letter} encerrou prematuramente com código {process.returncode}", is_error=True)
    
    # Manter o script em execução enquanto houver processos ativos ou bots esperados
    try:
        print_colored('Sistema', f"Monitorando {len(bots_to_run)} bot(s): {', '.join(bots_to_run)}")
        last_status_check = time.time()
        
        # Função para verificar timeouts de inatividade
        def check_inactivity_timeouts():
            current_time = time.time()
            bots_to_terminate = []
            
            with processes_lock:
                for bot_letter, process in list(processes.items()):
                    if process.poll() is None:  # Processo ainda ativo
                        time_since_last_activity = current_time - bot_last_activity.get(bot_letter, current_time)
                        
                        if time_since_last_activity > INACTIVITY_TIMEOUT:
                            bots_to_terminate.append((bot_letter, process, time_since_last_activity))
            
            # Encerrar ou reiniciar bots que excederam o timeout
            for bot_letter, process, inactive_time in bots_to_terminate:
                # Verificar se ainda pode tentar reiniciar
                if timeout_restart_counts[bot_letter] < max_timeout_restarts:
                    timeout_restart_counts[bot_letter] += 1
                    print_colored('Sistema', f"Bot {bot_letter} inativo por {int(inactive_time/60)} min - reiniciando ({timeout_restart_counts[bot_letter]}/{max_timeout_restarts})...", is_warning=True)
                    
                    # Encerrar o processo atual
                    try:
                        process.terminate()
                        time.sleep(3)
                        if process.poll() is None:
                            process.kill()
                    except Exception as e:
                        print_colored('Sistema', f"Erro ao encerrar Bot {bot_letter}: {str(e)}", is_error=True)
                    
                    # Remover do dicionário de processos
                    with processes_lock:
                        if bot_letter in processes:
                            del processes[bot_letter]
                    
                    # Resetar timestamp e reiniciar
                    bot_last_activity[bot_letter] = time.time()
                    bot_last_message[bot_letter] = "Bot reiniciado após timeout de inatividade (check ativo)"
                    
                    def restart_bot_check_wrapper(bl=bot_letter):
                        time.sleep(10)
                        new_process = start_delayed_bot(bl, 0, is_restart=True)
                        if new_process:
                            with processes_lock:
                                processes[bl] = new_process
                            bot_states[bl] = 'running'
                            print_colored('Sistema', f"Bot {bl} reiniciado com sucesso (verificação ativa).", is_success=True)
                        else:
                            print_colored('Sistema', f"Falha ao reiniciar Bot {bl}.", is_error=True)
                            bot_states[bl] = 'inactive_timeout'
                    
                    restart_thread = threading.Thread(target=restart_bot_check_wrapper)
                    restart_thread.daemon = False
                    restart_thread.start()
                else:
                    # Já tentou reiniciar, encerra definitivamente
                    print_colored('Sistema', f"Bot {bot_letter} inativo por {int(inactive_time/60)} min. Já reiniciado {max_timeout_restarts}x - encerrando definitivamente.", is_warning=True)
                    
                    # Marcar como encerrado por inatividade
                    bot_states[bot_letter] = 'inactive_timeout'
                    
                    # Enviar notificação para Discord
                    last_msg = bot_last_message.get(bot_letter, "Nenhuma atividade recente")
                    threading.Thread(target=send_discord_timeout_alert, args=(bot_letter, discord_webhook_url_br, discord_webhook_url_us, last_msg)).start()
                    
                    # Encerrar o processo
                    try:
                        process.terminate()
                        time.sleep(3)
                        if process.poll() is None:
                            process.kill()
                        print_colored('Sistema', f"Bot {bot_letter} encerrado definitivamente por timeout (verificação ativa).", is_warning=True)
                        
                        # Remover do dicionário de processos
                        with processes_lock:
                            if bot_letter in processes:
                                del processes[bot_letter]
                                
                    except Exception as e:
                        print_colored('Sistema', f"Erro ao encerrar Bot {bot_letter}: {str(e)}", is_error=True)
        
        while True:
            # Verificar timeouts de inatividade a cada ciclo
            check_inactivity_timeouts()
            
            # Verificar se ainda há processos ativos
            with processes_lock:
                active_processes = {k: v for k, v in processes.items() if v.poll() is None}
            
            # Log de status a cada 5 minutos (300 segundos) em vez de 30 segundos
            current_time = time.time()
            if current_time - last_status_check >= 300:
                if active_processes:
                    active_bots = ", ".join(active_processes.keys())
                    print_colored('Sistema', f"Status: {len(active_processes)} bot(s) ativo(s): {active_bots}")
                else:
                    # Mostrar estado detalhado quando não há processos ativos
                    completed = [bot for bot in bots_to_run if bot_states[bot] == 'completed']
                    failed = [bot for bot in bots_to_run if bot_states[bot] == 'failed'] 
                    banned = [bot for bot in bots_to_run if bot_states[bot] == 'banned']
                    timeout = [bot for bot in bots_to_run if bot_states[bot] == 'inactive_timeout']
                    still_running = [bot for bot in bots_to_run if bot_states[bot] == 'running']
                    
                    if completed:
                        print_colored('Sistema', f"Bots concluídos com sucesso: {', '.join(completed)}")
                    if failed:
                        print_colored('Sistema', f"Bots que falharam: {', '.join(failed)}")
                    if banned:
                        print_colored('Sistema', f"Bots banidos: {', '.join(banned)}")
                    if timeout:
                        print_colored('Sistema', f"Bots encerrados por timeout: {', '.join(timeout)}")
                    if still_running:
                        print_colored('Sistema', f"Bots ainda aguardando: {', '.join(still_running)}")
                    else:
                        print_colored('Sistema', "Nenhum bot aguardando execução.")
                last_status_check = current_time
            
            # Se não há processos ativos, verificar se devemos encerrar
            if not active_processes:
                # Contar bots por estado
                completed_bots = [bot for bot in bots_to_run if bot_states[bot] == 'completed']
                failed_bots = [bot for bot in bots_to_run if bot_states[bot] == 'failed']
                banned_bots_list = [bot for bot in bots_to_run if bot_states[bot] == 'banned']
                timeout_bots = [bot for bot in bots_to_run if bot_states[bot] == 'inactive_timeout']
                still_running = [bot for bot in bots_to_run if bot_states[bot] == 'running']
                
                # Se todos os bots terminaram (seja com sucesso, falha, banimento ou timeout), encerrar
                if not still_running:
                    print_colored('Sistema', f"Execução finalizada - Concluídos: {len(completed_bots)}, Falharam: {len(failed_bots)}, Banidos: {len(banned_bots_list)}, Timeout: {len(timeout_bots)}", is_success=True)
                    break
                
                # Se há bots ainda esperados mas que podem ser reiniciados, aguardar um pouco mais
                can_restart = [bot for bot in still_running if bot not in banned_bots and restart_counts[bot] < max_restarts and bot_states[bot] != 'inactive_timeout']
                if not can_restart:
                    print_colored('Sistema', "Todos os bots terminaram execução, falharam, estão banidos ou foram encerrados por timeout. Encerrando monitoramento.", is_success=True)
                    break
            
            time.sleep(1)
            
    except KeyboardInterrupt:
        print_colored('Sistema', "Interrupção detectada. Encerrando bots...")
        for bot_letter, process in processes.items():
            if process.poll() is None:
                print_colored('Sistema', f"Encerrando Bot {bot_letter}...")
                process.terminate()
                process.wait(timeout=5)
                if process.poll() is None:
                    process.kill()
    
    print_colored('Sistema', "Execução finalizada!")

def kill_all_bots():
    """
    Encerra todos os bots e seus processos filhos de forma mais robusta,
    garantindo que não haja processos persistentes ou logs de execuções anteriores.
    """
    global bot_pids, processes, restart_counts, is_shutdown_requested, banned_bots, last_banned_alerts
    
    # Sinaliza que um desligamento foi solicitado
    is_shutdown_requested = True
    print("🛑 Encerrando todos os bots e processos relacionados...")
    
    # Para cada bot principal
    for bot_letter in ['A', 'B', 'C', 'D', 'E']:
        # Obter os PIDs principais dos bots
        for pid in bot_pids.get(bot_letter, []):
            try:
                # Matar o processo e toda sua família com SIGKILL para garantir encerramento
                subprocess.run(f"pkill -9 -P {pid}", shell=True)
                # Garantir que o processo principal também seja encerrado
                subprocess.run(f"kill -9 {pid} 2>/dev/null", shell=True)
                print(f"✅ Bot {bot_letter}: Processo {pid} e seus filhos encerrados")
            except Exception as e:
                print(f"⚠️ Erro ao encerrar Bot {bot_letter} (PID {pid}): {str(e)}")
    
    # Limpar a lista de PIDs, contadores de reinicialização, bots banidos e alertas de banimento
    bot_pids = {key: [] for key in bot_pids}
    processes = {}  # Limpar o dicionário de processos
    restart_counts = {
        'A': 0,
        'B': 0,
        'C': 0,
        'D': 0,
        'E': 0
    }  # Resetar os contadores de reinicialização
    banned_bots.clear()  # Limpar a lista de bots banidos
    last_banned_alerts.clear()  # Limpar o histórico de alertas de banimento
    print("🔄 Lista de contas banidas e histórico de alertas foram limpos. Todos os bots podem ser reiniciados novamente.")
    
    # Garantir que não haja processos zumbis ou órfãos relacionados aos bots
    # Usar SIGKILL (-9) para garantir encerramento forçado
    subprocess.run(f"pkill -9 -f '{BOT_BASE_DIR_NAME}_[A-E]' 2>/dev/null", shell=True)
    subprocess.run(f"pkill -9 -f 'node.*{BOT_BASE_DIR_NAME}'", shell=True)
    subprocess.run("pkill -9 -f 'firefox'", shell=True, check=False)
    subprocess.run("pkill -9 -f 'chromium'", shell=True, check=False)
    subprocess.run("pkill -9 -f 'chrome'", shell=True, check=False)
    subprocess.run("pkill -9 -f 'thorium-browser'", shell=True, check=False)
    
    # Aguardar um momento para garantir que todos os processos foram encerrados
    time.sleep(5)
    
    # Limpar buffers de saída para evitar logs persistentes
    sys.stdout.flush()
    sys.stderr.flush()
    
    # Resetar a flag de shutdown após a limpeza completa
    is_shutdown_requested = False
    
    print("✅ Todos os bots foram encerrados e sistema reinicializado")
    
    # Retornar True para indicar sucesso na operação
    return True

def clean_error_message(error_message):
    """
    Limpa mensagens de erro para remover timestamps verbosos e IDs de processo,
    mantendo apenas as partes essenciais para logs mais concisos.
    """
    import re
    
    # Remove timestamps verbosos no formato [10/7/2025, 11:41:56 PM] ou similares
    cleaned = re.sub(r'\[\d{1,2}/\d{1,2}/\d{4},\s*\d{1,2}:\d{2}:\d{2}\s*[APM]{2}\]', '', error_message)
    
    # Remove IDs de processo no formato [7012] ou similares
    cleaned = re.sub(r'\[\d+\]', '', cleaned)
    
    # Remove múltiplos espaços em branco e limpa o início/fim
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    return cleaned

def send_discord_log_message(bot_account, message_content, discord_webhook_url_log):
    """Envia uma mensagem de log para o webhook do Discord especificado."""
    if not discord_webhook_url_log:
        print("⚠️ URL do webhook de log do Discord não configurada. Mensagem não enviada.")
        return

    try:
        current_time = time.strftime("%d/%m/%Y %H:%M:%S")
        log_message = f"📝 {bot_account} [{current_time}]: {message_content}"
        data = {
            "content": log_message
        }
        response = post_discord_with_custom_dns(discord_webhook_url_log, data)
        if response.status_code == 204:
            print(f"✅ Mensagem de log enviada para o Discord: {message_content}")
        else:
            print(f"❌ Erro ao enviar mensagem de log para o Discord: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Exceção ao enviar mensagem de log para o Discord: {str(e)}")

def stop_space(HF_TOKEN, SPACE_REPO_ID):
    api = HfApi(token=HF_TOKEN)
    print(f"🛑 Desligando o Space: {SPACE_REPO_ID}")
    try:
        api.delete_repo(repo_id=SPACE_REPO_ID, repo_type="space")
        print("Space deletado com sucesso.")
    except Exception as e:
        print(f"Erro ao deletar o Space: {e}")

def restart_space(HF_TOKEN, SPACE_REPO_ID, factory_reboot=True):
    api = HfApi(token=HF_TOKEN)
    reboot_type = "factory reboot" if factory_reboot else "restart"
    print(f"🔄 Reiniciando o Space ({reboot_type}): {SPACE_REPO_ID}")
    try:
        api.restart_space(repo_id=SPACE_REPO_ID, factory_reboot=factory_reboot)
        print(f"Space reiniciado com sucesso ({reboot_type}).")
    except Exception as e:
        print(f"Erro ao reiniciar o Space: {e}")


#TODOIST FUNCTIONS
HEADERS = {
    "Authorization": f"Bearer {TODOIST_API_TOKEN}",
    "Content-Type": "application/json"
}

def verificar_tarefa_concluida(nome_tarefa, projeto_id=None):
    if not TODOIST_API_TOKEN:
        # Token não definido, apenas retorna como se não tivesse tarefa
        return False
    try:
        # Se projeto_id foi especificado, filtra por projeto
        if projeto_id:
            response = requests.get(f"https://api.todoist.com/rest/v2/tasks?project_id={projeto_id}", headers=HEADERS)
        else:
            response = requests.get("https://api.todoist.com/rest/v2/tasks", headers=HEADERS)
            
        tarefas = response.json()
        for tarefa in tarefas:
            if tarefa["content"].lower() == nome_tarefa.lower():
                projeto_info = f" no projeto {projeto_id}" if projeto_id else ""
                print(f"[❌ A FAZER] Tarefa ainda ativa{projeto_info}: {tarefa['content']}")
                return False
        
        projeto_info = f" no projeto {projeto_id}" if projeto_id else ""
        print(f"[✅ CONCLUÍDA OU INEXISTENTE] '{nome_tarefa}' não está entre tarefas ativas{projeto_info}.")
        return True
    except Exception:
        # Falha silenciosa se não conseguir acessar a API
        return False

def concluir_tarefa(nome_tarefa, projeto_id=None):
    if not TODOIST_API_TOKEN:
        # Token não definido, retorna silenciosamente
        return False
    try:
        # Se projeto_id foi especificado, filtra por projeto
        if projeto_id:
            response = requests.get(f"https://api.todoist.com/rest/v2/tasks?project_id={projeto_id}", headers=HEADERS)
        else:
            response = requests.get("https://api.todoist.com/rest/v2/tasks", headers=HEADERS)
            
        tarefas = response.json()
        for tarefa in tarefas:
            if tarefa["content"].lower() == nome_tarefa.lower():
                tarefa_id = tarefa["id"]
                r = requests.post(f"https://api.todoist.com/rest/v2/tasks/{tarefa_id}/close", headers=HEADERS)
                if r.status_code == 204:
                    projeto_info = f" no projeto {projeto_id}" if projeto_id else ""
                    print(f"[✔️ CONCLUÍDA] Tarefa '{nome_tarefa}' concluída com sucesso{projeto_info}.")
                    return True
                else:
                    print(f"[⚠️ ERRO] Falha ao concluir tarefa '{nome_tarefa}' - Status: {r.status_code}")
                    return False
        
        projeto_info = f" no projeto {projeto_id}" if projeto_id else ""
        print(f"[⚠️ NÃO ENCONTRADA] Tarefa '{nome_tarefa}' não encontrada entre ativas{projeto_info}.")
        return False
    except Exception:
        # Falha silenciosa se não conseguir acessar a API
        return False

def criar_tarefa(nome_tarefa, projeto_id=None):
    if not TODOIST_API_TOKEN:
        # Token não definido, retorna silenciosamente
        return False
    try:
        # Se projeto_id foi especificado, filtra por projeto para verificar se já existe
        if projeto_id:
            response = requests.get(f"https://api.todoist.com/rest/v2/tasks?project_id={projeto_id}", headers=HEADERS)
        else:
            response = requests.get("https://api.todoist.com/rest/v2/tasks", headers=HEADERS)
            
        tarefas = response.json()
        for tarefa in tarefas:
            if tarefa["content"].lower() == nome_tarefa.lower():
                projeto_info = f" no projeto {projeto_id}" if projeto_id else ""
                print(f"[⚠️ JÁ EXISTE] Tarefa '{nome_tarefa}' já existe e está ativa{projeto_info}.")
                return False
                
        url = "https://api.todoist.com/rest/v2/tasks"
        payload = {"content": nome_tarefa}
        if projeto_id:
            payload["project_id"] = projeto_id
        response = requests.post(url, headers=HEADERS, json=payload)
        if response.status_code in (200, 204):
            projeto_info = f" no projeto {projeto_id}" if projeto_id else ""
            print(f"[✅ CRIADA] Tarefa '{nome_tarefa}' criada com sucesso{projeto_info}.")
            return True
        else:
            print(f"[⚠️ ERRO] Falha ao criar tarefa '{nome_tarefa}' - Status: {response.status_code}")
            print(response.text)
            return False
    except Exception:
        # Falha silenciosa se não conseguir acessar a API
        return False
