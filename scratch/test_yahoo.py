import urllib.request
import urllib.parse
import re

def test_yahoo():
    query = "POWERGRID stock price news"
    url = f"https://search.yahoo.com/search?q={urllib.parse.quote(query)}"
    print(f"Querying Yahoo Search: {url} ...")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")
            print(f"SUCCESS! Fetched {len(html)} bytes of HTML.")
            
            # Simple check to see if we got search results
            # Yahoo results are usually in <a> tags with class "d-al" or similar, or we can check for common text.
            if "results" in html or "class" in html:
                print("HTML contains typical search layout elements.")
                
            # Print a snippet of HTML to inspect
            print("\nSnippet of fetched HTML:")
            print(html[:500])
            
    except Exception as e:
        print(f"Yahoo Search failed: {e}")

if __name__ == "__main__":
    test_yahoo()
