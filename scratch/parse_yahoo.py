import urllib.request
import urllib.parse
import re

def parse_yahoo_results():
    query = "POWERGRID stock news"
    url = f"https://search.yahoo.com/search?q={urllib.parse.quote(query)}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")
            
            # Let's find all results blocks. In Yahoo, they are inside <div class="dd algo ...">
            # We can extract the links and descriptions.
            # Let's inspect the results blocks using regex.
            # Yahoo titles are usually in <h3 class="title"><a class="..." href="LINK">TITLE</a></h3>
            # Yahoo snippets are in <div class="compText aText"><p>SNIPPET</p></div> or <div class="compText">
            
            # Find all <div class="dd algo ..."> blocks
            blocks = re.findall(r'<div class="[^"]*algo[^"]*".*?</li>', html, re.DOTALL)
            print(f"Found {len(blocks)} raw result blocks.")
            
            parsed_results = []
            for block in blocks[:5]:
                # Extract link and title
                link_match = re.search(r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)
                if not link_match:
                    continue
                url_val = link_match.group(1)
                title_val = re.sub(r'<[^>]*>', '', link_match.group(2)) # strip HTML tags
                
                # Extract snippet
                snippet_match = re.search(r'<div class="[^"]*compText[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
                snippet_val = ""
                if snippet_match:
                    snippet_val = re.sub(r'<[^>]*>', '', snippet_match.group(1))
                
                parsed_results.append({
                    "title": title_val.strip(),
                    "url": url_val.strip(),
                    "snippet": snippet_val.strip()
                })
                
            print("\nParsed Results:")
            for idx, res in enumerate(parsed_results):
                print(f"{idx+1}. Title: {res['title']}")
                print(f"   URL:   {res['url']}")
                print(f"   Snippet: {res['snippet'][:150]}...")
                
    except Exception as e:
        print(f"Parsing failed: {e}")

if __name__ == "__main__":
    parse_yahoo_results()
