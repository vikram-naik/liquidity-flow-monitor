import os

def check_paths():
    print(f"Current WD: {os.getcwd()}")
    api_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"API Dir: {api_dir}")
    
    web_dir = os.path.join(api_dir, '..', 'web')
    print(f"Web Dir: {web_dir}")
    print(f"Web Dir Absolute: {os.path.abspath(web_dir)}")
    print(f"Web Dir exists: {os.path.isdir(web_dir)}")
    
    html_path = os.path.join(web_dir, 'dashboard.html')
    print(f"HTML Path: {html_path}")
    print(f"HTML Path exists: {os.path.isfile(html_path)}")
    
    if os.path.isdir(web_dir):
        print(f"Contents of {web_dir}:")
        print(os.listdir(web_dir))

if __name__ == "__main__":
    check_paths()
