import os
import subprocess
import importlib.util
import sys
import time
from dotenv import load_dotenv

# Carrega o arquivo .env
load_dotenv("configs.env")

# Garante que valores sejam strings e limpos
config_mode_env = str(os.getenv("CONFIG_MODE", "DEFAULT_CONFIG")).strip()
bot_directory_env = str(os.getenv("BOT_DIRECTORY", "")).strip()
bot_account_env = str(os.getenv("BOT_ACCOUNT", "")).strip()

bot_a_env = os.getenv("BOT_A", "False").strip().lower() == "true"
bot_b_env = os.getenv("BOT_B", "False").strip().lower() == "true"
bot_c_env = os.getenv("BOT_C", "False").strip().lower() == "true"
bot_d_env = os.getenv("BOT_D", "False").strip().lower() == "true"
bot_e_env = os.getenv("BOT_E", "False").strip().lower() == "true"

# URLs de Webhook do Discord carregadas do config.env
# Se não estiverem definidas, serão strings vazias.
discord_webhook_url_log_env = os.getenv("DISCORD_WEBHOOK_URL_LOG", "").strip()
discord_webhook_url_br_env = os.getenv("DISCORD_WEBHOOK_URL_BR", "").strip()
discord_webhook_url_us_env = os.getenv("DISCORD_WEBHOOK_URL_US", "").strip()

# API HuggingFace carregada do config.env
space_repo_id_env = str(os.getenv("SPACE_REPO_ID", "")).strip()
hf_token_env = "hf_" + str(os.getenv("HF_TOKEN", "")).strip()

# Define o basedir como o diretório atual
BASEDIR = os.getcwd()

# Define o nome base dos diretórios dos bots (facilita mudanças futuras)
BOT_BASE_DIR_NAME = "deepseek-v2"

# Carrega o rwds_functions.py
def load_functions():
    os.makedirs(BASEDIR, exist_ok=True)
    rwd_path = os.path.join(BASEDIR, "rwds_functions.py")
    spec = importlib.util.spec_from_file_location("rwds_functions", rwd_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["rwds_functions"] = module
    spec.loader.exec_module(module)
    return module

rwds_functions = load_functions()
current_ip = rwds_functions.get_current_ip()

def run_bots():
    os.chdir(BASEDIR)

    # Inicializar variável para controlar se a tarefa do IP foi criada
    ip_task_created = False

    # Usar as variáveis carregadas do .env diretamente
    BOT_DIRECTORY = bot_directory_env
    BOT_ACCOUNT = bot_account_env
    CONFIG_MODE = config_mode_env
    DISCORD_WEBHOOK_URL_LOG = discord_webhook_url_log_env
    DISCORD_WEBHOOK_URL_BR = discord_webhook_url_br_env
    DISCORD_WEBHOOK_URL_US = discord_webhook_url_us_env
    SPACE_REPO_ID = space_repo_id_env
    HF_TOKEN = hf_token_env
    

    rwds_functions.send_discord_log_message(BOT_ACCOUNT, "Iniciando execução...", DISCORD_WEBHOOK_URL_LOG)
    if CONFIG_MODE == "GEN_COOKIE_CONFIG":
        pass
    else:
        rwds_functions.criar_tarefa(BOT_ACCOUNT)



    folder_path = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_A")
    bot_selections = {
        "A": bot_a_env, "B": bot_b_env, "C": bot_c_env, 
        "D": bot_d_env, "E": bot_e_env
    }
    selected_bots = [bot for bot, selected in bot_selections.items() if selected]

    # Função auxiliar para executar subprocessos e capturar/yield sua saída
    def run_subprocess_and_print_output(command_list, description=""):
        """Executa um comando e transmite sua saída (stdout e stderr) via yield."""
        # Converte a lista de comandos para string para exibição no log
        command_str = ' '.join(command_list)
        if description:
            print(f"{description}...")
        try:
            process = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
            for line in iter(process.stdout.readline, ''):
                if line:
                    print(line.strip())
            process.stdout.close()
            return_code = process.wait()
            if return_code != 0:
                print(f"⚠️ Comando '{command_str}' falhou com código {return_code}")
            elif description:
                print(f"✅ {description} concluído.")
        except Exception as e:
            print(f"❌ Exceção ao executar '{command_str}': {str(e)}")

    # Debug inicial com print
    print(f"[DEBUG runner.py] CONFIG_MODE: '{CONFIG_MODE}'")
    print(f"[DEBUG runner.py] BOT_DIRECTORY: '{BOT_DIRECTORY}'")
    print(f"[DEBUG runner.py] BOT_ACCOUNT: '{BOT_ACCOUNT}'")
    print(f"[DEBUG runner.py] DISCORD_WEBHOOK_URL_US: '{DISCORD_WEBHOOK_URL_LOG}'")
    print(f"[DEBUG runner.py] DISCORD_WEBHOOK_URL_BR: '{DISCORD_WEBHOOK_URL_BR}'")
    print(f"[DEBUG runner.py] DISCORD_WEBHOOK_URL_US: '{DISCORD_WEBHOOK_URL_US}'")
    print(f"[DEBUG runner.py] SPACE_REPO_ID: '{SPACE_REPO_ID}'")
    print(f"[DEBUG runner.py] HF_TOKEN: '{HF_TOKEN}'")

    if not os.path.exists(folder_path):
        print("Instalando bots...")
        source_mv = os.path.join(BASEDIR, BOT_BASE_DIR_NAME)
        dest_mv = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_A")

        if os.path.exists(source_mv):
            run_subprocess_and_print_output(command_list=["mv", source_mv, dest_mv], description=f"Movendo {source_mv} para {dest_mv}")
        else:
            print(f"⚠️ '{source_mv}' não encontrado para mover. Verifique se o diretório base '{BOT_BASE_DIR_NAME}' existe.")

        src_copy = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_A")
        if os.path.exists(src_copy):
            for suffix in ["B", "C", "D", "E"]:
                dst_copy = os.path.join(BASEDIR, f"{BOT_BASE_DIR_NAME}_{suffix}")
                if os.path.exists(dst_copy):
                    run_subprocess_and_print_output(command_list=["rm", "-rf", dst_copy], description=f"Removendo destino existente {dst_copy}")
                run_subprocess_and_print_output(command_list=["cp", "-r", src_copy, dst_copy], description=f"Copiando {src_copy} para {dst_copy}")
            print("✅ Estrutura de diretórios dos bots criada.")
        else:
            print(f"⚠️ '{src_copy}' não encontrado para copiar. A etapa de mover/criar '{BOT_BASE_DIR_NAME}_A' pode ter falhado.")
    else:
        print("📁 Diretório já existe. Pulando instalação.")

    os.chdir(BASEDIR)

    print("Configurando ricronus e diretórios...")
    rwds_functions.setup_ricronus_and_directories(BOT_DIRECTORY)
    print("Copiando Rewards Drive...")
    rwds_functions.copy_rewards_drive(BOT_ACCOUNT)

    if selected_bots:
        print(f"Executando tarefas para os bots selecionados: {', '.join(selected_bots)}")
        rwds_functions.execute_tasks_for_selected_bots(BOT_DIRECTORY, BOT_ACCOUNT, CONFIG_MODE, *selected_bots)
        
        print("Iniciando bots...")
        rwds_functions.start_bots(DISCORD_WEBHOOK_URL_BR, DISCORD_WEBHOOK_URL_US, *selected_bots) # Saída principal ainda no console
        print("✅ Bots executados e encerrados.")

        if CONFIG_MODE == "GEN_COOKIE_CONFIG":
            pass
        else:
            print("Concluindo tarefa...")
            rwds_functions.concluir_tarefa(BOT_ACCOUNT) 
            # Só concluir tarefa do IP se ela foi criada
            if ip_task_created:
                rwds_functions.concluir_tarefa(current_ip, "6cjh8V9GcVr6r4x7") 
        time.sleep(5)
        print("Fazendo upload de sessions para o google drive...")
        rwds_functions.upload_rewards_drive(BOT_ACCOUNT)
    else:
        print("⚠️ Nenhum bot foi selecionado.")

    if SPACE_REPO_ID:
        rwds_functions.send_discord_log_message(BOT_ACCOUNT, "Execução finalizada, desligando Space.", DISCORD_WEBHOOK_URL_LOG)
        time.sleep(5)
        rwds_functions.stop_space(HF_TOKEN, SPACE_REPO_ID)
        print("🏁 Processo concluído.")
        time.sleep(180)
    else:
        rwds_functions.send_discord_log_message(BOT_ACCOUNT, "Execução finalizada, encerrando GITHUB Action.", DISCORD_WEBHOOK_URL_LOG)
        print("🏁 Processo concluído.")


if __name__ == '__main__':
    run_bots()

