from flask import Flask, request, render_template_string, send_file, jsonify
import os
import fitz  # PyMuPDF
import pandas as pd
import xml.etree.ElementTree as ET
import pyreadstat
import uuid
import tempfile
import shutil
import zipfile
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# Create uploads directory
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ---------------- File Processing Functions ----------------

def replace_text_in_pdf(input_pdf_path, old_text, new_text):
    """Replace text in PDF file"""
    try:
        pdf_document = fitz.open(input_pdf_path)
        font_name = "Times-Roman"
        
        for page in pdf_document:
            text_instances = page.search_for(old_text)
            if text_instances:
                original_text_info = page.get_text("dict")['blocks']
                
                for rect in text_instances:
                    page.add_redact_annot(rect)
                page.apply_redactions()
                
                for rect in text_instances:
                    original_fontsize = 12
                    for block in original_text_info:
                        if 'lines' in block:
                            for line in block.get("lines", []):
                                for span in line.get("spans", []):
                                    if old_text in span.get("text", ""):
                                        original_fontsize = span.get("size", 12)
                                        break
                    
                    font_params = {
                        'fontsize': original_fontsize,
                        'fontname': font_name
                    }
                    insert_point = fitz.Point(rect.x0, rect.y1 - 2.5)
                    page.insert_text(insert_point, new_text, **font_params)
        
        output_path = input_pdf_path.replace('.pdf', '_modified.pdf')
        pdf_document.save(output_path)
        pdf_document.close()
        return output_path
    except Exception as e:
        print(f"Error processing PDF: {str(e)}")
        return None

def replace_text_in_csv(input_csv_path, old_text, new_text):
    """Replace text in CSV file"""
    try:
        df = pd.read_csv(input_csv_path, dtype=str)
        # Handle NaN values
        df = df.fillna('')
        # Replace text in all string columns
        for col in df.columns:
            df[col] = df[col].astype(str).str.replace(old_text, new_text, regex=False)
        
        output_path = input_csv_path.replace('.csv', '_modified.csv')
        df.to_csv(output_path, index=False)
        return output_path
    except Exception as e:
        print(f"Error processing CSV: {str(e)}")
        return None

def replace_text_in_xml(input_xml_path, old_text, new_text):
    """Replace text in XML file"""
    try:
        tree = ET.parse(input_xml_path)
        root = tree.getroot()
        
        def replace_in_element(elem):
            if elem.text and old_text in elem.text:
                elem.text = elem.text.replace(old_text, new_text)
            if elem.tail and old_text in elem.tail:
                elem.tail = elem.tail.replace(old_text, new_text)
            for k, v in elem.attrib.items():
                if old_text in v:
                    elem.attrib[k] = v.replace(old_text, new_text)
            for child in elem:
                replace_in_element(child)
        
        replace_in_element(root)
        
        output_path = input_xml_path.replace('.xml', '_modified.xml')
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        return output_path
    except Exception as e:
        print(f"Error processing XML: {str(e)}")
        return None

def replace_text_in_xpt(input_xpt_path, old_text, new_text):
    """Replace text in XPT file"""
    try:
        df, meta = pyreadstat.read_xport(input_xpt_path)
        # Handle NaN values
        df = df.fillna('')
        # Replace text in all string columns
        for col in df.columns:
            df[col] = df[col].astype(str).str.replace(old_text, new_text, regex=False)
        
        output_path = input_xpt_path.replace('.xpt', '_modified.xpt')
        table_name = meta.table_name if meta.table_name else 'DATA'
        pyreadstat.write_xport(df, output_path, file_format_version=8, table_name=table_name)
        return output_path
    except Exception as e:
        print(f"Error processing XPT: {str(e)}")
        return None

def process_single_file(file_path, old_text, new_text):
    """Process a single file based on its extension"""
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == '.pdf':
        return replace_text_in_pdf(file_path, old_text, new_text)
    elif ext == '.csv':
        return replace_text_in_csv(file_path, old_text, new_text)
    elif ext == '.xml':
        return replace_text_in_xml(file_path, old_text, new_text)
    elif ext == '.xpt':
        return replace_text_in_xpt(file_path, old_text, new_text)
    else:
        return None

def extract_zip_and_process(zip_path, old_text, new_text):
    """Extract ZIP file and process all supported files inside"""
    extract_folder = os.path.join(UPLOAD_FOLDER, f"extracted_{uuid.uuid4()}")
    os.makedirs(extract_folder)
    
    processed_files = []
    supported_extensions = ['.pdf', '.csv', '.xml', '.xpt']
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_folder)
        
        # Process all files in the extracted folder
        for root, dirs, files in os.walk(extract_folder):
            for file in files:
                file_path = os.path.join(root, file)
                ext = os.path.splitext(file)[1].lower()
                
                if ext in supported_extensions:
                    try:
                        processed_file = process_single_file(file_path, old_text, new_text)
                        if processed_file:
                            processed_files.append(processed_file)
                    except Exception as e:
                        print(f"Error processing {file}: {str(e)}")
                        continue
        
        # Create a new ZIP with processed files
        if processed_files:
            output_zip = zip_path.replace('.zip', '_modified.zip')
            with zipfile.ZipFile(output_zip, 'w') as zip_ref:
                for processed_file in processed_files:
                    zip_ref.write(processed_file, os.path.basename(processed_file))
            
            return output_zip
        else:
            return None
            
    finally:
        # Clean up extracted folder
        if os.path.exists(extract_folder):
            shutil.rmtree(extract_folder, ignore_errors=True)

# ---------------- Routes ----------------

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        old_text = request.form.get('old_text', '').strip()
        new_text = request.form.get('new_text', '').strip()
        
        if not old_text:
            return jsonify({'error': 'Text to find is required'}), 400
        
        # Handle multiple files
        uploaded_files = request.files.getlist('pdf_file')
        if not uploaded_files or all(file.filename == '' for file in uploaded_files):
            return jsonify({'error': 'No files selected'}), 400
        
        processed_files = []
        temp_files = []
        supported_extensions = ['.pdf', '.csv', '.xml', '.xpt', '.zip']
        
        for file in uploaded_files:
            if file.filename == '':
                continue
                
            filename = secure_filename(file.filename)
            ext = os.path.splitext(filename)[1].lower()
            
            if ext not in supported_extensions:
                return jsonify({'error': f'Unsupported file type: {ext}. Supported types: PDF, CSV, XML, XPT, ZIP'}), 400
            
            # Save uploaded file
            unique_filename = f"{uuid.uuid4()}_{filename}"
            file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
            file.save(file_path)
            temp_files.append(file_path)
            
            # Process file
            try:
                if ext == '.zip':
                    output_path = extract_zip_and_process(file_path, old_text, new_text)
                else:
                    output_path = process_single_file(file_path, old_text, new_text)
                
                if output_path:
                    processed_files.append({
                        'path': output_path,
                        'name': f"modified_{filename}"
                    })
                else:
                    return jsonify({'error': f'Failed to process {filename}'}), 400
                    
            except Exception as e:
                # Clean up on error
                for temp_file in temp_files:
                    try:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                    except:
                        pass
                return jsonify({'error': f'Error processing {filename}: {str(e)}'}), 500
        
        if not processed_files:
            return jsonify({'error': 'No files were processed successfully'}), 400
        
        # If only one file processed, send it directly
        if len(processed_files) == 1:
            response = send_file(
                processed_files[0]['path'],
                as_attachment=True,
                download_name=processed_files[0]['name'],
                mimetype='application/octet-stream'
            )
        else:
            # Create a ZIP file with all processed files
            zip_filename = f"modified_files_{uuid.uuid4()}.zip"
            zip_path = os.path.join(UPLOAD_FOLDER, zip_filename)
            
            with zipfile.ZipFile(zip_path, 'w') as zip_ref:
                for processed_file in processed_files:
                    zip_ref.write(processed_file['path'], processed_file['name'])
            
            processed_files.append({'path': zip_path, 'name': zip_filename})
            
            response = send_file(
                zip_path,
                as_attachment=True,
                download_name=zip_filename,
                mimetype='application/zip'
            )
        
        # Clean up files after a delay
        import threading
        def cleanup_files():
            import time
            time.sleep(30)  # Wait 30 seconds before cleanup
            try:
                for temp_file in temp_files:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                for processed_file in processed_files:
                    if os.path.exists(processed_file['path']):
                        os.remove(processed_file['path'])
            except Exception as e:
                print(f"Cleanup error: {e}")
        
        cleanup_thread = threading.Thread(target=cleanup_files)
        cleanup_thread.daemon = True
        cleanup_thread.start()
        
        return response
        
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500

# ---------------- HTML Template ----------------

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Document Text Replacer</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        
        .container {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
            padding: 30px;
            width: 100%;
            max-width: 900px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 30px;
            position: relative;
            overflow: hidden;
        }
        
        .container::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: linear-gradient(90deg, #667eea, #764ba2);
        }
        
        .left-panel {
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        
        .right-panel {
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            gap: 20px;
        }
        
        .logo-text {
            color: #dc3545;
            font-size: 1rem;
            font-weight: 700;
            text-align: center;
            margin-bottom: 2px;
            grid-column: 1 / -1;
        }
        
        h1 {
            color: #333;
            font-size: 2.2rem;
            font-weight: 700;
            text-align: center;
            margin-bottom: 5px;
            grid-column: 1 / -1;
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 20px;
            grid-column: 1 / -1;
            font-size: 1rem;
        }
        
        .form-group {
            position: relative;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: 600;
            font-size: 0.95rem;
            transition: color 0.3s ease;
        }
        
        input[type="text"] {
            width: 100%;
            padding: 15px 20px;
            border: 2px solid #e1e5e9;
            border-radius: 12px;
            font-size: 1rem;
            transition: all 0.3s ease;
            background: rgba(255, 255, 255, 0.9);
        }
        
        input[type="text"]:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
            transform: translateY(-2px);
        }
        
        .file-upload-area {
            position: relative;
            border: 2px dashed #e1e5e9;
            border-radius: 12px;
            padding: 40px 20px;
            text-align: center;
            transition: all 0.3s ease;
            cursor: pointer;
            background: rgba(255, 255, 255, 0.5);
        }
        
        .file-upload-area:hover {
            border-color: #667eea;
            background: rgba(102, 126, 234, 0.05);
            transform: translateY(-2px);
        }
        
        .file-upload-area.dragover {
            border-color: #667eea;
            background: rgba(102, 126, 234, 0.1);
            transform: scale(1.02);
        }
        
        .upload-icon {
            font-size: 3rem;
            color: #667eea;
            margin-bottom: 15px;
            display: block;
            transition: transform 0.3s ease;
        }
        
        .file-upload-area:hover .upload-icon {
            transform: scale(1.1);
        }
        
        .upload-text {
            color: #666;
            font-size: 1.1rem;
            margin-bottom: 10px;
            font-weight: 500;
        }
        
        .upload-subtext {
            color: #999;
            font-size: 0.9rem;
        }
        
        input[type="file"] {
            position: absolute;
            opacity: 0;
            width: 100%;
            height: 100%;
            cursor: pointer;
        }
        
        .file-info {
            margin-top: 15px;
            padding: 10px;
            background: rgba(102, 126, 234, 0.1);
            border-radius: 8px;
            color: #667eea;
            font-weight: 500;
            display: none;
            max-height: 150px;
            overflow-y: auto;
        }
        
        .file-item {
            padding: 5px 0;
            border-bottom: 1px solid rgba(102, 126, 234, 0.1);
        }
        
        .file-item:last-child {
            border-bottom: none;
        }
        
        .btn {
            padding: 15px 30px;
            border: none;
            border-radius: 12px;
            font-size: 1.1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            min-width: 200px;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white;
            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.3);
        }
        
        .btn-primary:hover:not(:disabled) {
            transform: translateY(-3px);
            box-shadow: 0 12px 35px rgba(102, 126, 234, 0.4);
        }
        
        .btn-success {
            background: linear-gradient(135deg, #28a745, #20c997);
            color: white;
            box-shadow: 0 8px 25px rgba(40, 167, 69, 0.3);
        }
        
        .btn-success:hover {
            transform: translateY(-3px);
            box-shadow: 0 12px 35px rgba(40, 167, 69, 0.4);
        }
        
        .btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none !important;
        }
        
        .spinner {
            display: none;
            margin-top: 20px;
        }
        
        .spinner.show {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            color: #667eea;
            font-weight: 500;
        }
        
        .spinner::after {
            content: '';
            width: 20px;
            height: 20px;
            border: 2px solid #e1e5e9;
            border-top: 2px solid #667eea;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .status-message {
            padding: 15px 20px;
            border-radius: 12px;
            margin-top: 20px;
            font-weight: 500;
            text-align: center;
            display: none;
            animation: slideIn 0.5s ease;
        }
        
        .status-success {
            background: rgba(40, 167, 69, 0.1);
            color: #28a745;
            border: 1px solid rgba(40, 167, 69, 0.2);
        }
        
        .status-error {
            background: rgba(220, 53, 69, 0.1);
            color: #dc3545;
            border: 1px solid rgba(220, 53, 69, 0.2);
        }
        
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateY(-10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .process-steps {
            display: flex;
            flex-direction: column;
            gap: 12px;
            margin-top: 15px;
        }
        
        .step {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px;
            background: rgba(255, 255, 255, 0.7);
            border-radius: 10px;
            transition: all 0.3s ease;
        }
        
        .step-number {
            width: 30px;
            height: 30px;
            background: #667eea;
            color: white;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 0.9rem;
        }
        
        .step-text {
            color: #666;
            font-size: 0.95rem;
        }
        
        @media (max-width: 768px) {
            .container {
                grid-template-columns: 1fr;
                gap: 30px;
                padding: 30px 20px;
            }
            
            h1 {
                font-size: 2rem;
            }
            
            .btn {
                min-width: 100%;
            }
        }
        
        .file-types {
            color: #999;
            font-size: 0.85rem;
            margin-top: 10px;
            text-align: center;
        }
        
        .multiple-files-info {
            background: rgba(102, 126, 234, 0.1);
            border-radius: 8px;
            padding: 10px;
            margin-top: 10px;
            font-size: 0.85rem;
            color: #667eea;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo-text">Local Development Version</div>
        <h1>Document Text Replacer</h1>
        <p class="subtitle">Replace text in PDF, CSV, XML, XPT files and ZIP folders with ease</p>
        
        <div class="left-panel">
            <div class="form-group">
                <label for="old_text">🔍 Text to Find</label>
                <input type="text" id="old_text" placeholder="Enter the text you want to replace..." required>
            </div>
            
            <div class="form-group">
                <label for="new_text">✏️ Replace With</label>
                <input type="text" id="new_text" placeholder="Enter the replacement text...">
            </div>
            
            <div class="form-group">
                <label>📁 Upload Documents</label>
                <div class="file-upload-area" id="fileUploadArea">
                    <span class="upload-icon">⬆️</span>
                    <div class="upload-text">Drop your files here or click to browse</div>
                    <div class="upload-subtext">Supports PDF, CSV, XML, XPT files and ZIP folders</div>
                    <input type="file" id="pdf-file-input" accept=".pdf,.csv,.xml,.xpt,.zip" multiple>
                    <div class="file-info" id="fileInfo"></div>
                </div>
                <div class="file-types">Supported formats: PDF, CSV, XML, XPT, ZIP (Max 50MB total)</div>
                <div class="multiple-files-info">
                    💡 You can select multiple files or upload a ZIP folder containing supported files
                </div>
            </div>
        </div>
        
        <div class="right-panel">
            <div class="process-steps">
                <div class="step">
                    <div class="step-number">1</div>
                    <div class="step-text">Enter the text you want to find and replace</div>
                </div>
                <div class="step">
                    <div class="step-number">2</div>
                    <div class="step-text">Upload multiple documents or a ZIP folder</div>
                </div>
                <div class="step">
                    <div class="step-number">3</div>
                    <div class="step-text">Click process to generate your modified files</div>
                </div>
            </div>
            
            <button type="button" class="btn btn-primary" id="processBtn" disabled>
                <span>🚀 Process Documents</span>
            </button>
            
            <div class="spinner" id="loadingSpinner">
                <span>Processing your documents...</span>
            </div>
            
            <div class="status-message" id="statusMessage"></div>
            
            <button type="button" class="btn btn-success" id="downloadBtn" style="display: none;">
                <span>⬇️ Download Modified Files</span>
            </button>
        </div>
    </div>
    
    <script>
        const fileInput = document.getElementById('pdf-file-input');
        const fileUploadArea = document.getElementById('fileUploadArea');
        const fileInfo = document.getElementById('fileInfo');
        const processBtn = document.getElementById('processBtn');
        const downloadBtn = document.getElementById('downloadBtn');
        const spinner = document.getElementById('loadingSpinner');
        const statusMessage = document.getElementById('statusMessage');
        const oldTextInput = document.getElementById('old_text');
        const newTextInput = document.getElementById('new_text');
        
        let currentFiles = [];
        
        // File upload handling
        fileUploadArea.addEventListener('click', () => fileInput.click());
        
        fileUploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            fileUploadArea.classList.add('dragover');
        });
        
        fileUploadArea.addEventListener('dragleave', () => {
            fileUploadArea.classList.remove('dragover');
        });
        
        fileUploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            fileUploadArea.classList.remove('dragover');
            const files = Array.from(e.dataTransfer.files);
            if (files.length > 0) {
                handleFileSelect(files);
            }
        });
        
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                handleFileSelect(Array.from(e.target.files));
            }
        });
        
        function handleFileSelect(files) {
            const allowedTypes = ['.pdf', '.csv', '.xml', '.xpt', '.zip'];
            const validFiles = [];
            let totalSize = 0;
            
            for (const file of files) {
                const fileExt = '.' + file.name.split('.').pop().toLowerCase();
                
                if (!allowedTypes.includes(fileExt)) {
                    showStatus(`Invalid file type: ${file.name}. Please select PDF, CSV, XML, XPT, or ZIP files.`, 'error');
                    return;
                }
                
                totalSize += file.size;
                if (totalSize > 50 * 1024 * 1024) {
                    showStatus('Total file size must be less than 50MB', 'error');
                    return;
                }
                
                validFiles.push(file);
            }
            
            currentFiles = validFiles;
            
            // Display file info
            if (validFiles.length === 1) {
                fileInfo.innerHTML = `📄 ${validFiles[0].name} (${formatFileSize(validFiles[0].size)})`;
            } else {
                const fileList = validFiles.map(file => 
                    `<div class="file-item">📄 ${file.name} (${formatFileSize(file.size)})</div>`
                ).join('');
                fileInfo.innerHTML = `<div><strong>${validFiles.length} files selected:</strong></div>${fileList}`;
            }
            
            fileInfo.style.display = 'block';
            checkFormValidity();
        }
        
        function formatFileSize(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }
        
        // Form validation
        function checkFormValidity() {
            const hasFiles = currentFiles.length > 0;
            const hasOldText = oldTextInput.value.trim() !== '';
            
            processBtn.disabled = !(hasFiles && hasOldText);
        }
        
        oldTextInput.addEventListener('input', checkFormValidity);
        newTextInput.addEventListener('input', checkFormValidity);
        
        // Process button click
        processBtn.addEventListener('click', async () => {
            if (currentFiles.length === 0 || !oldTextInput.value.trim()) {
                showStatus('Please select files and enter text to find', 'error');
                return;
            }
            
            const formData = new FormData();
            
            // Append all files
            currentFiles.forEach(file => {
                formData.append('pdf_file', file);
            });
            
            formData.append('old_text', oldTextInput.value.trim());
            formData.append('new_text', newTextInput.value.trim());
            
            setProcessingState(true);
            
            try {
                const response = await fetch('/upload', {
                    method: 'POST',
                    body: formData
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.error || 'Processing failed');
                }
                
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                
                // Determine download filename
                let filename;
                if (currentFiles.length === 1) {
                    filename = `modified_${currentFiles[0].name}`;
                } else {
                    filename = 'modified_files.zip';
                }
                
                // Store download URL for later use
                downloadBtn.setAttribute('data-url', url);
                downloadBtn.setAttribute('data-filename', filename);
                
                showStatus('✅ Documents processed successfully!', 'success');
                downloadBtn.style.display = 'flex';
                
            } catch (error) {
                showStatus(`❌ Error: ${error.message}`, 'error');
            } finally {
                setProcessingState(false);
            }
        });
        
        // Download button click
        downloadBtn.addEventListener('click', () => {
            const url = downloadBtn.getAttribute('data-url');
            const filename = downloadBtn.getAttribute('data-filename');
            
            if (url && filename) {
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
                
                showStatus('📥 Download started!', 'success');
            }
        });
        
        function setProcessingState(processing) {
            processBtn.disabled = processing;
            if (processing) {
                spinner.classList.add('show');
                downloadBtn.style.display = 'none';
                hideStatus();
            } else {
                spinner.classList.remove('show');
            }
        }
        
        function showStatus(message, type) {
            statusMessage.textContent = message;
            statusMessage.className = `status-message status-${type}`;
            statusMessage.style.display = 'block';
            
            if (type === 'error') {
                setTimeout(() => hideStatus(), 5000);
            }
        }
        
        function hideStatus() {
            statusMessage.style.display = 'none';
        }
        
        // Add some interactive animations
        document.querySelectorAll('.btn').forEach(btn => {
            btn.addEventListener('mouseenter', function() {
                if (!this.disabled) {
                    this.style.transform = 'translateY(-3px)';
                }
            });
            
            btn.addEventListener('mouseleave', function() {
                if (!this.disabled) {
                    this.style.transform = 'translateY(0)';
                }
            });
        });
        
        document.querySelectorAll('input[type="text"]').forEach(input => {
            input.addEventListener('focus', function() {
                this.parentElement.querySelector('label').style.color = '#667eea';
            });
            
            input.addEventListener('blur', function() {
                this.parentElement.querySelector('label').style.color = '#555';
            });
        });
    </script>
</body>
</html>'''

if __name__ == '__main__':
    print("Starting Flask application...")
    print("Server will be available at: http://127.0.0.1:5000")
    print("Press CTRL+C to stop the server")
    app.run(debug=True, host='127.0.0.1', port=5000)