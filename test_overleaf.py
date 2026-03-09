import os
from utils.overleaf_connector import OverleafConnector

def test():
    connector = OverleafConnector()
    project_name = "MyFreeTestProject"
    
    # We construct the path to the local project folder
    project_path = os.path.join(connector.base_storage_path, project_name)
    
    print("--- Testing LaTeX Cleaner on Local Drop Folder ---")
    
    # Read and clean the main.tex file
    clean_text = connector.read_and_clean_tex_file(project_path, "main.tex")
    
    if clean_text:
        print("\nCleaned Text Output (Ready for LLM):")
        print("=" * 40)
        print(clean_text)
        print("=" * 40)
    else:
        print("Failed to read the file. Make sure 'main.tex' is inside 'overleaf_projects/MyFreeTestProject'")

if __name__ == "__main__":
    test()