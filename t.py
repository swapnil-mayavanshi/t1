from flask import Flask, request, render_template, send_file, jsonify
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

# Create uploads and templates directories
UPLOAD_FOLDER = 'uploads'
TEMPLATES_FOLDER = 'templates'

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

if not os.path.exists(TEMPLATES_FOLDER):
    os.makedirs(TEMPLATES_FOLDER)

# ---------------- File Processing Functions ----------------

def replace_text_in_pdf(input_pdf_path, old_text, new_text):
    """Replace text in PDF file"""
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
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            if old_text in span["text"]:
                                original_fontsize = span["size"]
                                break
                        else:
                            continue
                        break
                    else:
                        continue
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

def replace_text_in_csv(input_csv_path, old_text, new_text):
    """Replace text in CSV file"""
    df = pd.read_csv(input_csv_path, dtype=str)
    df = df.applymap(lambda x: x.replace(old_text, new_text) if isinstance(x, str) else x)
    
    output_path = input_csv_path.replace('.csv', '_modified.csv')
    df.to_csv(output_path, index=False)
    return output_path

def replace_text_in_xml(input_xml_path, old_text, new_text):
    """Replace text in XML file"""
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

def replace_text_in_xpt(input_xpt_path, old_text, new_text):
    """Replace text in XPT file"""
    df, meta = pyreadstat.read_xport(input_xpt_path)
    df = df.applymap(lambda x: x.replace(old_text, new_text) if isinstance(x, str) else x)
    
    output_path = input_xpt_path.replace('.xpt', '_modified.xpt')
    pyreadstat.write_xport(df, output_path, file_format_version=8, table_name=meta.table_name)
    return output_path

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
        shutil.rmtree(extract_folder, ignore_errors=True)

# ---------------- Routes ----------------

@app.route('/')
def index():
    return render_template('index.html')

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
            
            processed_files.append({'path': zip_path, 'name': zip_filename})  # Add zip to cleanup list
            
            response = send_file(
                zip_path,
                as_attachment=True,
                download_name=zip_filename,
                mimetype='application/zip'
            )
        
        # Clean up files after sending
        def remove_files():
            try:
                for temp_file in temp_files:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                for processed_file in processed_files:
                    if os.path.exists(processed_file['path']):
                        os.remove(processed_file['path'])
            except Exception as e:
                print(f"Error cleaning up files: {e}")
        
        # Schedule cleanup (in production, use a proper background task)
        import threading
        threading.Timer(15.0, remove_files).start()
        
        return response
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Local development settings
    app.run(debug=True)
