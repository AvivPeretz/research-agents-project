import os
import re
import logging
import requests
import zipfile
import io

class OverleafConnector:
    """
    A utility class to download Overleaf projects using free Read-Only links,
    extract the TeX files, and clean them for LLM processing.
    """
    def __init__(self, base_storage_path: str = "overleaf_projects"):
        self.base_storage_path = base_storage_path
        self._create_directory(self.base_storage_path)
        
        self.logger = logging.getLogger("OverleafConnector")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)

    def _create_directory(self, path: str):
        """Helper method to create directories if they do not exist."""
        if not os.path.exists(path):
            os.makedirs(path)

    def download_project_source(self, read_link: str, project_name: str) -> str:
        """
        Downloads the Overleaf project source as a ZIP using a Read-Only link
        and extracts it locally.
        """
        self.logger.info("Analyzing Overleaf read link...")
        
        # Extract the unique project token from the read link
        # Example link: https://www.overleaf.com/read/abcdefghijk
        token_match = re.search(r'read/([a-zA-Z0-9]+)', read_link)
        if not token_match:
            self.logger.error("Invalid Overleaf read link format. Must contain '/read/TOKEN'")
            return ""
        
        token = token_match.group(1)
        # Overleaf's hidden endpoint to download a ZIP from a read-token
        download_url = f"https://www.overleaf.com/project/{token}/download/zip"
        project_path = os.path.join(self.base_storage_path, project_name)
        
        try:
            self.logger.info("Downloading project ZIP for '%s'...", project_name)
            response = requests.get(download_url)
            response.raise_for_status() # Check if the download succeeded
            
            # Extract the downloaded ZIP file directly into our folder
            self.logger.info("Extracting files to %s...", project_path)
            with zipfile.ZipFile(io.BytesIO(response.content)) as zip_ref:
                zip_ref.extractall(project_path)
                
            self.logger.info("Successfully downloaded and extracted project: %s", project_name)
            return project_path
            
        except Exception as e:
            self.logger.error("Failed to download or extract project. Details: %s", str(e))
            return ""

    def clean_latex_text(self, raw_tex: str) -> str:
        """
        Cleans basic LaTeX formatting commands from the text to make it readable for the LLM.
        """
        self.logger.info("Cleaning LaTeX formatting to extract plain text...")
        
        # 1. Remove comments
        clean_text = re.sub(r'%.*$', '', raw_tex, flags=re.MULTILINE)
        
        # 2. Remove entire setup commands and environments we don't need to read
        clean_text = re.sub(r'\\documentclass\[.*?\]\{.*?\}|\\documentclass\{.*?\}', '', clean_text)
        clean_text = re.sub(r'\\usepackage\[.*?\]\{.*?\}|\\usepackage\{.*?\}', '', clean_text)
        clean_text = re.sub(r'\\begin\{document\}|\\end\{document\}', '', clean_text)
        
        # 3. Extract text from formatting commands (e.g., \textbf{Hello} -> Hello)
        clean_text = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', clean_text)
        
        # 4. Remove standalone commands (like \maketitle, \clearpage)
        clean_text = re.sub(r'\\[a-zA-Z]+\*?', '', clean_text)
        
        # 5. Clean up multiple empty lines and curly braces
        clean_text = clean_text.replace('{', '').replace('}', '')
        clean_text = re.sub(r'\n\s*\n', '\n\n', clean_text)
        
        return clean_text.strip()

    def read_and_clean_tex_file(self, project_path: str, main_file: str = "main.tex") -> str:
        """
        Reads the main .tex file of the project and returns the cleaned text.
        """
        file_path = os.path.join(project_path, main_file)
        if not os.path.exists(file_path):
            self.logger.error("File not found: %s", file_path)
            return ""
            
        with open(file_path, 'r', encoding='utf-8') as file:
            raw_tex = file.read()
            
        return self.clean_latex_text(raw_tex)