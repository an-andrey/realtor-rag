from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import sqlite3
import re
import time

DB_PATH = "db/londono_propreties.db"

def setup_database(db_name=DB_PATH):
    print(f"[DB] Initializing database: {db_name}")
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS properties (
            centris_id TEXT PRIMARY KEY,
            address TEXT,
            price TEXT,
            bedrooms TEXT,
            bathrooms TEXT,
            property_type TEXT,
            url TEXT
        )
    ''')
    conn.commit()
    return conn

def get_property_ids(driver, start_page=1, max_pages=3):
    property_ids = set()
    base_search_url = "https://www.londonogroup.com/property/search.php"
    
    for page in range(start_page, start_page + max_pages):
        # Construct the URL with parameters
        url = f"{base_search_url}?district=any&prop_type=any&broker=any&project=any&price_range=any&sales=1&rentals=1&subSearch=submit&page={page}&sort=recent"
        print(f"[SEARCH] Loading search page {page}: {url}")
        
        driver.get(url)
        # Pause to allow JavaScript to render the property items
        time.sleep(3) 
        
        html_content = driver.page_source
        soup = BeautifulSoup(html_content, 'html.parser')
        items = soup.find_all('a', class_='project-item')
        
        if not items:
            print(f"[SEARCH] No property items found on page {page}. Halting search.")
            break
            
        print(f"[SEARCH] Found {len(items)} property elements on page {page}.")
        
        for item in items:
            desc_div = item.find('div', class_='desc')
            if desc_div:
                match = re.search(r'CENTRIS #:\s*(\d+)', desc_div.text)
                if match:
                    centris_id = match.group(1)
                    property_ids.add(centris_id)
                    print(f"[SEARCH] Extracted ID: {centris_id}")
                else:
                    print("[SEARCH] Warning: Found description div but failed to parse Centris ID.")
            else:
                print("[SEARCH] Warning: Item missing description div.")
                
    return list(property_ids)

def parse_property_page(driver, centris_id):
    url = f"https://www.londonogroup.com/property/viewProperty.php?id={centris_id}"
    print(f"[PARSE] Loading property page for ID {centris_id}: {url}")
    
    driver.get(url)
    time.sleep(2)
    
    html_content = driver.page_source
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Check if the page loaded an actual property or redirected to an error
    address_tag = soup.find('h2', class_='details-title')
    if not address_tag:
        print(f"[PARSE] Error: Missing address title. Page may not have loaded correctly for {centris_id}.")
        return None
        
    address = " ".join(address_tag.stripped_strings)
    print(f"[PARSE] Address found: {address}")
        
    price = "N/A"
    price_tag = soup.find('div', class_='price-inn')
    if price_tag:
        price = " ".join(price_tag.stripped_strings)
        
    bedrooms = "N/A"
    bathrooms = "N/A"
    features_list = soup.find('ul', class_='total-price')
    if features_list:
        for li in features_list.find_all('li'):
            text = li.text.lower()
            if 'bedrooms' in text:
                bedrooms = li.find('strong').text if li.find('strong') else text
            elif 'bathrooms' in text:
                bathrooms = li.find('strong').text if li.find('strong') else text
                
    property_type = "N/A"
    details_list = soup.find('ul', class_='list-details')
    if details_list:
        for li in details_list.find_all('li'):
            if 'Property Type' in li.text:
                span = li.find('span')
                property_type = span.text if span else li.text
                break
                
    print(f"[PARSE] Extraction complete for {centris_id} | Price: {price} | Type: {property_type}")
    return (centris_id, address, price, bedrooms, bathrooms, property_type, url)

def main():
    print("[SYSTEM] Starting scraper script.")
    conn = setup_database()
    cursor = conn.cursor()
    
    print("[SYSTEM] Launching Chrome browser.")
    options = Options()
    options.add_argument("--headless=new")  # Required for snap in headless environments
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    # Ubuntu Snap places the native ARM64 chromedriver here:
    chrome_service = Service(executable_path="/snap/bin/chromium.chromedriver")

    driver = webdriver.Chrome(service=chrome_service, options=options)
    # The browser will run in non-headless mode to allow visual monitoring
    
    try:
        centris_ids = get_property_ids(driver, start_page=1, max_pages=2)
        print(f"[SYSTEM] Total unique properties identified: {len(centris_ids)}")
        
        for cid in centris_ids:
            property_data = parse_property_page(driver, cid)
            
            if property_data:
                print(f"[DB] Saving ID {cid} to database.")
                cursor.execute('''
                    INSERT OR REPLACE INTO properties 
                    (centris_id, address, price, bedrooms, bathrooms, property_type, url)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', property_data)
                conn.commit()
            else:
                print(f"[DB] Skipping ID {cid} due to parsing failure.")
                
            time.sleep(1)
            
    except Exception as e:
        print(f"[SYSTEM] An unexpected error occurred: {e}")
        
    finally:
        print("[SYSTEM] Closing browser and database connection.")
        driver.quit()
        conn.close()

if __name__ == "__main__":
    main()