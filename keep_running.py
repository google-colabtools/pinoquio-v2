from flask import Flask, render_template_string, send_from_directory, abort, url_for, request, redirect, send_file
import os
from werkzeug.utils import secure_filename
import time
import zipfile
import tempfile
import shutil

app = Flask(__name__)

# Adicione isso no início do seu arquivo, após criar o app Flask
@app.after_request
def allow_iframe(response):
    response.headers.pop('X-Frame-Options', None)
    return response

# Define o diretório base para o explorador de arquivos
BASE_DIR = os.getcwd()

START_TIME = time.time()

@app.route('/', methods=['GET', 'POST'])
@app.route('/<path:subpath>', methods=['GET', 'POST'])
def home(subpath=''):
    safe_base_dir = os.path.abspath(BASE_DIR)
    requested_path = os.path.abspath(os.path.join(safe_base_dir, subpath))
    uptime_seconds = int(time.time() - START_TIME)

    if not requested_path.startswith(safe_base_dir) or not os.path.exists(requested_path):
        abort(404)

    if os.path.isdir(requested_path):
        if request.method == 'POST':
            # Verifica se há arquivos no request
            if 'files' not in request.files:
                return redirect(request.url)
            
            files = request.files.getlist('files')
            uploaded_count = 0
            
            for file in files:
                if file and file.filename != '':
                    filename = secure_filename(file.filename)
                    if filename:  # Garante que o filename é válido
                        file.save(os.path.join(requested_path, filename))
                        uploaded_count += 1
            
            # Se pelo menos um arquivo foi enviado, redireciona
            if uploaded_count > 0:
                return redirect(url_for('home', subpath=subpath))

        items = sorted(os.listdir(requested_path), key=str.lower)
        
        # Lista de pastas e arquivos para ocultar no explorador
        hidden_items = {'.git', '.github', '__pycache__', 'venv', '.venv', 'node_modules', '.env'}
        
        dirs = [item for item in items if os.path.isdir(os.path.join(requested_path, item)) and item not in hidden_items]
        files = [item for item in items if os.path.isfile(os.path.join(requested_path, item)) and item != 'accounts.json' and item not in hidden_items]

        path_parts = subpath.split('/') if subpath else []
        breadcrumbs = [{'name': 'home', 'path': url_for('home')}]
        for i, part in enumerate(path_parts):
            if part:
                breadcrumbs.append({'name': part, 'path': url_for('home', subpath='/'.join(path_parts[:i+1]))})

        return render_template_string(HOME_TEMPLATE, dirs=dirs, files=files, current_path=subpath, breadcrumbs=breadcrumbs, uptime_seconds=uptime_seconds)
    else:
        directory = os.path.dirname(requested_path)
        filename = os.path.basename(requested_path)
        # Lista de extensões que devem ser forçadamente baixadas (consideradas "binárias")
        binary_extensions = [
            '.zip', '.rar', '.7z', '.tar', '.gz',  # Arquivos compactados
            '.exe', '.msi', '.dmg', '.deb',        # Executáveis e instaladores
            '.bin', '.iso', '.img', '.dll', '.so', # Imagens de disco e binários
            '.doc', '.docx', '.xls', '.xlsx',     # Documentos do Office que não são bem visualizados
            '.ppt', '.pptx',
            '.mp3', '.wav', '.mp4', '.avi', '.mkv' # Arquivos de mídia que são melhores para baixar
        ]

        # Força o download se a extensão estiver na lista de binários
        force_download = any(filename.lower().endswith(ext) for ext in binary_extensions)

        return send_from_directory(directory, filename, as_attachment=force_download)

# Template HTML principal com topbar e explorador de arquivos
HOME_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>App is Running - File Explorer</title>
    <style>
        body { 
            background: #23272e; 
            color: #c7d0dc; 
            font-family: 'Segoe UI', 'Arial', sans-serif; 
            margin: 0; 
            padding: 0;
        }
        
        /* Topbar */
        .topbar {
            background: #2c313c;
            padding: 15px 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.3);
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        
        .app-status {
            display: flex;
            align-items: center;
            gap: 15px;
        }
        
        .status-indicator {
            width: 12px;
            height: 12px;
            background: #28a745;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }
        
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }
        
        .status-text {
            color: #7ecfff;
            font-weight: 600;
            font-size: 1.1em;
        }
        
        .uptime-info {
            color: #c7d0dc;
            font-size: 1em;
        }
        
        .uptime-value {
            color: #7ecfff;
            font-weight: 600;
        }

        .log-btn {
            background-color: #ffc107;
            color: #23272e;
            border: none;
            padding: 8px 20px;
            border-radius: 6px;
            font-weight: 700;
            font-size: 0.95em;
            cursor: pointer;
            transition: background-color 0.3s, transform 0.1s;
            letter-spacing: 1px;
        }
        .log-btn:hover {
            background-color: #ffda4a;
            transform: scale(1.05);
        }
        .log-btn:active {
            transform: scale(0.97);
        }

        /* Log Modal */
        .log-overlay {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.7);
            z-index: 9999;
            justify-content: center;
            align-items: center;
        }
        .log-overlay.active {
            display: flex;
        }
        .log-modal {
            background: #23272e;
            border: 1px solid #4f5b6a;
            border-radius: 12px;
            width: 90vw;
            height: 80vh;
            max-width: 1100px;
            display: flex;
            flex-direction: column;
            box-shadow: 0 8px 40px rgba(0,0,0,0.6);
        }
        .log-modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 14px 20px;
            background: #2c313c;
            border-radius: 12px 12px 0 0;
            border-bottom: 1px solid #4f5b6a;
        }
        .log-modal-header span {
            color: #7ecfff;
            font-weight: 700;
            font-size: 1.1em;
        }
        .log-modal-header .log-status {
            color: #888;
            font-size: 0.85em;
            font-weight: 400;
        }
        .log-close-btn {
            background: #e74c3c;
            color: white;
            border: none;
            padding: 6px 14px;
            border-radius: 5px;
            cursor: pointer;
            font-weight: 600;
            transition: background-color 0.3s;
        }
        .log-close-btn:hover {
            background: #c0392b;
        }
        .log-content {
            flex: 1;
            overflow-y: auto;
            padding: 16px 20px;
            font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
            font-size: 0.85em;
            line-height: 1.6;
            white-space: pre-wrap;
            word-break: break-all;
            color: #c7d0dc;
        }
        
        /* Container principal */
        .container { 
            background: #2c313c; 
            margin: 20px auto; 
            padding: 20px 40px; 
            border-radius: 12px; 
            box-shadow: 0 4px 24px rgba(0,0,0,0.4); 
            max-width: 900px; 
        }
        
        h1, h2 { 
            color: #7ecfff; 
            border-bottom: 1px solid #4f5b6a; 
            padding-bottom: 10px; 
        }
        
        ul { 
            list-style-type: none; 
            padding: 0; 
        }
        
        li { 
            padding: 8px 12px; 
            border-bottom: 1px solid #3a424d; 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
        }
        
        li:last-child { 
            border-bottom: none; 
        }
        
        a { 
            color: #7ecfff; 
            text-decoration: none; 
            font-weight: 500; 
        }
        
        a:hover { 
            text-decoration: underline; 
        }
        
        .dir::before { 
            content: '📁'; 
            margin-right: 10px; 
        }
        
        .file::before { 
            content: '📄'; 
            margin-right: 10px; 
        }
        
        .breadcrumbs { 
            margin-bottom: 20px; 
            padding: 10px; 
            background-color: #23272e; 
            border-radius: 5px; 
        }
        
        .breadcrumbs a { 
            color: #c7d0dc; 
        }
        
        .breadcrumbs span { 
            color: #777; 
            margin: 0 5px; 
        }
        
        form { 
            margin-top: 20px; 
            padding: 15px; 
            background-color: #3a424d; 
            border-radius: 8px; 
        }
        
        input[type="file"] { 
            color: #c7d0dc; 
            margin-bottom: 10px; 
            width: 100%; 
        }
        
        input[type="submit"] {
            background-color: #7ecfff; 
            color: #23272e; 
            border: none;
            padding: 8px 16px; 
            border-radius: 5px; 
            font-weight: 600; 
            cursor: pointer;
            transition: background-color 0.3s;
        }
        
        input[type="submit"]:hover { 
            background-color: #a2e0ff; 
        }
        
        .upload-info { 
            font-size: 12px; 
            color: #888; 
            margin-top: 5px; 
        }
        
        .item-actions { 
            display: flex; 
            gap: 10px; 
            align-items: center; 
        }
        
        .download-btn {
            background-color: #28a745; 
            color: white; 
            border: none;
            padding: 4px 8px; 
            border-radius: 4px; 
            font-size: 12px;
            cursor: pointer; 
            text-decoration: none;
            transition: background-color 0.3s;
        }
        
        .download-btn:hover { 
            background-color: #218838; 
        }
        
        .spoiler {
            margin: 20px 0;
            border: 1px solid #4f5b6a;
            border-radius: 8px;
            background-color: #3a424d;
        }
        
        .spoiler-header {
            padding: 15px;
            cursor: pointer;
            background-color: #2c313c;
            border-radius: 8px 8px 0 0;
            user-select: none;
            transition: background-color 0.3s;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .spoiler-header:hover {
            background-color: #343a47;
        }
        
        .spoiler-header h2 {
            margin: 0;
            border-bottom: none;
            padding-bottom: 0;
        }
        
        .spoiler-arrow {
            transition: transform 0.3s;
            font-size: 16px;
        }
        
        .spoiler-arrow.open {
            transform: rotate(90deg);
        }
        
        .spoiler-content {
            padding: 0 15px;
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease, padding 0.3s ease;
        }
        
        .spoiler-content.open {
            max-height: 500px;
            padding: 15px;
        }
    </style>
    <script>
        let uptime = {{ uptime_seconds }};
        
        function formatUptime(s) {
            let h = Math.floor(s/3600);
            let m = Math.floor((s%3600)/60);
            let sec = s%60;
            return h.toString().padStart(2,'0')+':'+m.toString().padStart(2,'0')+':'+sec.toString().padStart(2,'0');
        }
        
        function updateUptime() {
            uptime += 1;
            document.getElementById('uptime').innerText = formatUptime(uptime);
        }
        
        function toggleSpoiler(element) {
            const content = element.nextElementSibling;
            const arrow = element.querySelector('.spoiler-arrow');
            
            if (content.classList.contains('open')) {
                content.classList.remove('open');
                arrow.classList.remove('open');
            } else {
                content.classList.add('open');
                arrow.classList.add('open');
            }
        }

        let logInterval = null;
        function openLog() {
            document.getElementById('logOverlay').classList.add('active');
            fetchLog();
            logInterval = setInterval(fetchLog, 1000);
        }
        function closeLog() {
            document.getElementById('logOverlay').classList.remove('active');
            if (logInterval) { clearInterval(logInterval); logInterval = null; }
        }
        function fetchLog() {
            const statusEl = document.getElementById('logStatus');
            statusEl.innerText = 'Atualizando...';
            fetch('/api/log')
                .then(r => r.json())
                .then(data => {
                    const el = document.getElementById('logText');
                    el.textContent = data.content;
                    el.scrollTop = el.scrollHeight;
                    statusEl.innerText = 'Atualizado: ' + new Date().toLocaleTimeString();
                })
                .catch(() => {
                    statusEl.innerText = 'Erro ao carregar log';
                });
        }
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') closeLog();
        });
        
        window.onload = function() {
            document.getElementById('uptime').innerText = formatUptime(uptime);
            setInterval(updateUptime, 1000);
        };
    </script>
</head>
<body>
    <!-- Log Modal -->
    <div class="log-overlay" id="logOverlay" onclick="if(event.target===this)closeLog()">
        <div class="log-modal">
            <div class="log-modal-header">
                <span>📋 Runner Log <span class="log-status" id="logStatus"></span></span>
                <button class="log-close-btn" onclick="closeLog()">✕ Fechar</button>
            </div>
            <div class="log-content" id="logText">Carregando...</div>
        </div>
    </div>

    <!-- Topbar -->
    <div class="topbar">
        <div class="app-status">
            <div class="status-indicator"></div>
            <span class="status-text">App is Running...</span>
        </div>
        <button class="log-btn" onclick="openLog()">📋 LOG</button>
        <div class="uptime-info">
            Uptime: <span class="uptime-value" id="uptime"></span>
        </div>
    </div>

    <!-- Conteúdo Principal -->
    <div class="container">
        <h1>📁 File Explorer</h1>
        <div class="breadcrumbs">
            {% for crumb in breadcrumbs %}
                <a href="{{ crumb.path }}">{{ crumb.name }}</a>
                {% if not loop.last %}<span>/</span>{% endif %}
            {% endfor %}
        </div>

        <div class="spoiler">
            <div class="spoiler-header" onclick="toggleSpoiler(this)">
                <h2>📤 Upload de Arquivos</h2>
                <span class="spoiler-arrow">▶</span>
            </div>
            <div class="spoiler-content">
                <form method="post" enctype="multipart/form-data">
                    <input type="file" name="files" multiple required>
                    <div class="upload-info">💡 Você pode selecionar múltiplos arquivos segurando Ctrl (Windows/Linux) ou Cmd (Mac)</div>
                    <input type="submit" value="Upload">
                </form>
            </div>
        </div>

        <h2>Diretórios</h2>
        <ul>
            {% for dir in dirs %}
            <li>
                <a class="dir" href="{{ url_for('home', subpath=(current_path + '/' if current_path else '') + dir) }}">{{ dir }}</a>
                <div class="item-actions">
                    <a href="{{ url_for('download_folder', subpath=(current_path + '/' if current_path else '') + dir) }}" class="download-btn">📥 ZIP</a>
                </div>
            </li>
            {% else %}
            <li>Nenhum diretório encontrado.</li>
            {% endfor %}
        </ul>

        <h2>Arquivos</h2>
        <ul>
            {% for file in files %}
            <li>
                <a class="file" href="{{ url_for('home', subpath=(current_path + '/' if current_path else '') + file) }}">{{ file }}</a>
                <div class="item-actions">
                    <a href="{{ url_for('home', subpath=(current_path + '/' if current_path else '') + file) }}" class="download-btn">📥 Download</a>
                </div>
            </li>
            {% else %}
            <li>Nenhum arquivo encontrado.</li>
            {% endfor %}
        </ul>
    </div>
</body>
</html>
"""

# Template HTML para o explorador de arquivos (mantido para compatibilidade)
FILE_EXPLORER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>File Explorer</title>
    <style>
        body { background: #23272e; color: #c7d0dc; font-family: 'Segoe UI', 'Arial', sans-serif; margin: 20px; }
        .container { background: #2c313c; padding: 20px 40px; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,0.4); max-width: 900px; margin: auto; }
        h1, h2 { color: #7ecfff; border-bottom: 1px solid #4f5b6a; padding-bottom: 10px; }
        ul { list-style-type: none; padding: 0; }
        li { padding: 8px 12px; border-bottom: 1px solid #3a424d; display: flex; justify-content: space-between; align-items: center; }
        li:last-child { border-bottom: none; }
        a { color: #7ecfff; text-decoration: none; font-weight: 500; }
        a:hover { text-decoration: underline; }
        .dir::before { content: '📁'; margin-right: 10px; }
        .file::before { content: '📄'; margin-right: 10px; }
        .breadcrumbs { margin-bottom: 20px; padding: 10px; background-color: #23272e; border-radius: 5px; }
        .breadcrumbs a { color: #c7d0dc; }
        .breadcrumbs span { color: #777; margin: 0 5px; }
        form { margin-top: 20px; padding: 15px; background-color: #3a424d; border-radius: 8px; }
        input[type="file"] { color: #c7d0dc; margin-bottom: 10px; width: 100%; }
        input[type="submit"] {
            background-color: #7ecfff; color: #23272e; border: none;
            padding: 8px 16px; border-radius: 5px; font-weight: 600; cursor: pointer;
            transition: background-color 0.3s;
        }
        input[type="submit"]:hover { background-color: #a2e0ff; }
        .upload-info { font-size: 12px; color: #888; margin-top: 5px; }
        .item-actions { display: flex; gap: 10px; align-items: center; }
        .download-btn {
            background-color: #28a745; color: white; border: none;
            padding: 4px 8px; border-radius: 4px; font-size: 12px;
            cursor: pointer; text-decoration: none;
            transition: background-color 0.3s;
        }
        .download-btn:hover { background-color: #218838; }
        .spoiler {
            margin: 20px 0;
            border: 1px solid #4f5b6a;
            border-radius: 8px;
            background-color: #3a424d;
        }
        .spoiler-header {
            padding: 15px;
            cursor: pointer;
            background-color: #2c313c;
            border-radius: 8px 8px 0 0;
            user-select: none;
            transition: background-color 0.3s;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .spoiler-header:hover {
            background-color: #343a47;
        }
        .spoiler-header h2 {
            margin: 0;
            border-bottom: none;
            padding-bottom: 0;
        }
        .spoiler-arrow {
            transition: transform 0.3s;
            font-size: 16px;
        }
        .spoiler-arrow.open {
            transform: rotate(90deg);
        }
        .spoiler-content {
            padding: 0 15px;
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease, padding 0.3s ease;
        }
        .spoiler-content.open {
            max-height: 500px;
            padding: 15px;
        }
    </style>
    <script>
        function toggleSpoiler(element) {
            const content = element.nextElementSibling;
            const arrow = element.querySelector('.spoiler-arrow');
            
            if (content.classList.contains('open')) {
                content.classList.remove('open');
                arrow.classList.remove('open');
            } else {
                content.classList.add('open');
                arrow.classList.add('open');
            }
        }
    </script>
</head>
<body>
    <div class="container">
        <h1>File Explorer</h1>
        <div class="breadcrumbs">
            {% for crumb in breadcrumbs %}
                <a href="{{ crumb.path }}">{{ crumb.name }}</a>
                {% if not loop.last %}<span>/</span>{% endif %}
            {% endfor %}
        </div>

        <div class="spoiler">
            <div class="spoiler-header" onclick="toggleSpoiler(this)">
                <h2>📤 Upload de Arquivos</h2>
                <span class="spoiler-arrow">▶</span>
            </div>
            <div class="spoiler-content">
                <form method="post" enctype="multipart/form-data">
                    <input type="file" name="files" multiple required>
                    <div class="upload-info">💡 Você pode selecionar múltiplos arquivos segurando Ctrl (Windows/Linux) ou Cmd (Mac)</div>
                    <input type="submit" value="Upload">
                </form>
            </div>
        </div>

        <h2>Diretórios</h2>
        <ul>
            {% for dir in dirs %}
            <li>
                <a class="dir" href="{{ url_for('file_explorer', subpath=(current_path + '/' if current_path else '') + dir) }}">{{ dir }}</a>
                <div class="item-actions">
                    <a href="{{ url_for('download_folder', subpath=(current_path + '/' if current_path else '') + dir) }}" class="download-btn">📥 ZIP</a>
                </div>
            </li>
            {% else %}
            <li>Nenhum diretório encontrado.</li>
            {% endfor %}
        </ul>

        <h2>Arquivos</h2>
        <ul>
            {% for file in files %}
            <li>
                <a class="file" href="{{ url_for('file_explorer', subpath=(current_path + '/' if current_path else '') + file) }}">{{ file }}</a>
                <div class="item-actions">
                    <a href="{{ url_for('file_explorer', subpath=(current_path + '/' if current_path else '') + file) }}" class="download-btn">📥 Download</a>
                </div>
            </li>
            {% else %}
            <li>Nenhum arquivo encontrado.</li>
            {% endfor %}
        </ul>
    </div>
</body>
</html>
"""

LOG_FILE = os.path.join(BASE_DIR, 'runner.log')

@app.route('/api/log')
def api_log():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', errors='replace') as f:
                content = f.read()
        else:
            content = '(runner.log não encontrado)'
    except Exception as e:
        content = f'Erro ao ler log: {e}'
    from flask import jsonify
    return jsonify({'content': content})

@app.route('/download_folder/<path:subpath>')
def download_folder(subpath):
    safe_base_dir = os.path.abspath(BASE_DIR)
    folder_path = os.path.abspath(os.path.join(safe_base_dir, subpath))
    
    if not folder_path.startswith(safe_base_dir) or not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        abort(404)
    
    # Cria um arquivo temporário ZIP
    temp_dir = tempfile.mkdtemp()
    folder_name = os.path.basename(folder_path)
    zip_filename = f"{folder_name}.zip"
    zip_path = os.path.join(temp_dir, zip_filename)
    
    try:
        # Cria o arquivo ZIP
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, folder_path)
                    zipf.write(file_path, arcname)
        
        # Envia o arquivo ZIP
        return send_file(zip_path, as_attachment=True, download_name=zip_filename, mimetype='application/zip')
    
    except Exception as e:
        # Limpa o diretório temporário em caso de erro
        shutil.rmtree(temp_dir, ignore_errors=True)
        abort(500)

@app.route('/files/', methods=['GET', 'POST'])
@app.route('/files/<path:subpath>', methods=['GET', 'POST'])
def file_explorer(subpath=''):
    # Redireciona para a nova rota principal
    return redirect(url_for('home', subpath=subpath))

if __name__ == "__main__":
    app.run()