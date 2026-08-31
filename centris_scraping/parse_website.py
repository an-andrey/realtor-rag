import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup


DB_PATH = "db/londono_properties.db"
BASE_URL = "https://www.londonogroup.com"


PROPERTY_COLUMNS = [
    "centris_id",
    "url",
    "address",
    "street_address",
    "city_region",
    "postal_code",
    "province",
    "listing_type",
    "transaction_type",
    "status",
    "price",
    "price_amount",
    "bedrooms",
    "bathrooms",
    "powder_rooms",
    "property_type",
    "building_type",
    "year_built",
    "living_area",
    "building_size",
    "lot_size",
    "lot_area",
    "municipality_tax",
    "school_tax",
    "condo_fees",
    "description",
    "addendums",
    "inclusions",
    "exclusions",
    "geocode_address",
    "nearby_json",
    "key_features_json",
    "details_json",
    "features_json",
    "sections_json",
    "brokers_json",
    "image_urls_json",
    "raw_text",
    "scraped_at",
]


def clean_text(value, separator=" "):
    if value is None:
        return None
    if hasattr(value, "stripped_strings"):
        text = separator.join(value.stripped_strings)
    else:
        text = str(value)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\bm\s+2\b", "m2", text)
    return text or None


def normalize_label(label):
    label = clean_text(label) or ""
    label = label.replace("#", "number")
    label = re.sub(r"[^0-9A-Za-z]+", "_", label.lower()).strip("_")
    return label or "unknown"


def dumps(value):
    return json.dumps(value or [], ensure_ascii=False, sort_keys=True)


def parse_money_amount(text):
    if not text:
        return None
    match = re.search(r"[-+]?\$?\s*([0-9][0-9,\s]*(?:\.[0-9]{2})?)", text)
    if not match:
        return None
    return float(match.group(1).replace(",", "").replace(" ", ""))


def parse_int(text):
    if not text:
        return None
    match = re.search(r"\d+", text.replace(",", ""))
    return int(match.group(0)) if match else None


def first_value(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, list):
            value = next((item for item in value if item), None)
        if value:
            return value
    return None


def append_grouped(mapping, label, value):
    label = clean_text(label)
    value = clean_text(value)
    if not label or not value:
        return
    mapping.setdefault(label, [])
    if value not in mapping[label]:
        mapping[label].append(value)


def extract_address(soup):
    title = soup.select_one("h2.details-title.hoa-hidemobile") or soup.select_one("h2.details-title")
    if not title:
        return {}

    lines = [clean_text(line) for line in title.stripped_strings]
    lines = [line for line in lines if line]
    address = clean_text(" ".join(lines))
    street_address = lines[0] if lines else address
    city_region = lines[1] if len(lines) > 1 else None
    postal_code = None

    if address:
        match = re.search(r"\b([A-Z]\d[A-Z]\s?\d[A-Z]\d)\b", address, re.I)
        if match:
            postal_code = match.group(1).upper().replace(" ", "")

    return {
        "address": address,
        "street_address": street_address,
        "city_region": city_region,
        "postal_code": postal_code,
        "province": "QC",
    }


def extract_listing_type_and_price(soup):
    price_text = clean_text(soup.select_one(".price-inn"))
    listing_input = soup.select_one(".frmScheduleShowing .listing_type")
    listing_type = listing_input.get("value") if listing_input else None
    transaction_type = None
    price = None

    if price_text:
        match = re.search(r"For\s+(Sale|Rent)\s*:\s*(.+)", price_text, re.I)
        if match:
            transaction_type = "buy" if match.group(1).lower() == "sale" else "rent"
            price = clean_text(match.group(2))
        else:
            price = price_text

    if not transaction_type and listing_type:
        transaction_type = "rent" if listing_type.lower() == "rental" else "buy"

    return {
        "listing_type": listing_type,
        "transaction_type": transaction_type,
        "price": price,
        "price_amount": parse_money_amount(price),
    }


def parse_label_value_items(items):
    rows = []
    grouped = {}

    for item in items:
        span = item.find("span")
        strong = item.find("strong")
        value_node = strong or span
        value = clean_text(value_node)

        if value_node:
            label = clean_text(item.get_text(" ", strip=True).replace(value_node.get_text(" ", strip=True), "", 1))
        else:
            text = clean_text(item)
            if not text or ":" not in text:
                continue
            label, value = text.split(":", 1)
            label = clean_text(label)
            value = clean_text(value)

        label = clean_text((label or "").rstrip(":"))
        if not label or not value:
            continue

        rows.append({"label": label, "key": normalize_label(label), "value": value})
        append_grouped(grouped, label, value)

    return rows, grouped


def extract_key_features(soup):
    rows, grouped = parse_label_value_items(soup.select("ul.total-price li"))
    values_by_key = {}
    for row in rows:
        values_by_key.setdefault(row["key"], [])
        if row["value"] not in values_by_key[row["key"]]:
            values_by_key[row["key"]].append(row["value"])
    return rows, grouped, values_by_key


def extract_detail_sections(soup):
    sections = []
    details_by_section = {}
    features = []
    nearby = []

    details_content = soup.select_one(".details-content")
    if not details_content:
        return sections, details_by_section, features, nearby

    for heading in details_content.find_all(["h4", "h5"], class_="title"):
        title = clean_text(heading)
        if not title:
            continue

        detail_list = heading.find_next_sibling("ul", class_="list-details")
        if not detail_list:
            continue

        rows, grouped = parse_label_value_items(detail_list.find_all("li", recursive=False))
        if not rows:
            continue

        section = {"title": title, "items": rows}
        sections.append(section)
        details_by_section[title] = grouped

        if title.lower() == "features":
            features = rows
            nearby = [row["value"] for row in rows if row["key"] == "proximity" and row["value"]]

    return sections, details_by_section, features, nearby


def extract_narrative_sections(soup):
    sections = {}
    section_rows = []

    for desc in soup.select(".desc-content"):
        title = clean_text(desc.select_one("h4.title"))
        body = clean_text(desc.select_one("p.more"), separator="\n")
        if not title or not body:
            continue
        sections[normalize_label(title)] = body
        section_rows.append({"title": title, "key": normalize_label(title), "body": body})

    return sections, section_rows


def extract_brokers(soup):
    brokers = []
    seen = set()

    for member in soup.select(".vnp-member"):
        name = clean_text(member.select_one(".name"))
        title = clean_text(member.select_one("h4.title"))
        phone_link = member.select_one('a[href^="tel:"]')
        phone = clean_text(phone_link.get("href", "").replace("tel:", "")) if phone_link else clean_text(member.select_one(".phone"))
        profile_link = member.select_one('a[href*="viewBroker.php"]')
        profile_url = urljoin(BASE_URL, profile_link.get("href")) if profile_link else None
        broker_id = None
        if profile_url:
            match = re.search(r"broker=(\d+)", profile_url)
            broker_id = match.group(1) if match else None

        email = None
        contact_link = member.select_one('a[onclick*="emailBroker"]')
        if contact_link:
            match = re.search(r"emailBroker\('([^']+)'\)", contact_link.get("onclick", ""))
            email = match.group(1) if match else None
        if not email:
            email_input = member.find_next_sibling("input", id=re.compile(r"broker.*email", re.I))
            email = email_input.get("value") if email_input else None

        key = broker_id or email or name
        if not key or key in seen:
            continue
        seen.add(key)

        brokers.append({
            "broker_id": broker_id,
            "name": name,
            "title": title,
            "phone": phone,
            "email": email,
            "profile_url": profile_url,
        })

    return brokers


def extract_images(soup):
    urls = []
    for tag in soup.select(".item-slick-inn a[href], a.rsImg[href], meta[property='og:image'], link[rel='image_src']"):
        url = tag.get("href") or tag.get("content")
        if url and "mediaserver.centris.ca" in url and url not in urls:
            urls.append(url)
    return urls


def extract_geocode_address(soup):
    script_text = "\n".join(script.get_text("\n") for script in soup.find_all("script"))
    match = re.search(r"window\.geocode_address\s*=\s*'([^']+)'", script_text)
    return clean_text(match.group(1)) if match else None


def extract_status(soup):
    status_tag = soup.select_one(".property_status_tag")
    status = clean_text(status_tag)
    return status


def parse_property_html(html_content, centris_id, url):
    soup = BeautifulSoup(html_content, "html.parser")

    address_data = extract_address(soup)
    if not address_data.get("address"):
        print(f"[PARSE] Error: Missing address title for {centris_id}.")
        return None

    key_feature_rows, key_features_by_label, key_features_by_key = extract_key_features(soup)
    detail_sections, details_by_section, feature_rows, nearby = extract_detail_sections(soup)
    narrative, narrative_rows = extract_narrative_sections(soup)
    general_info = details_by_section.get("General Information", {})

    property_type = first_value(general_info, "Property Type")
    data = {
        "centris_id": centris_id,
        "url": url,
        **address_data,
        **extract_listing_type_and_price(soup),
        "status": extract_status(soup),
        "bedrooms": parse_int(first_value(key_features_by_key, "bedrooms")),
        "bathrooms": parse_int(first_value(key_features_by_key, "bathrooms")),
        "powder_rooms": parse_int(first_value(key_features_by_key, "powder_rooms")),
        "property_type": property_type,
        "building_type": first_value(general_info, "Building type"),
        "year_built": parse_int(first_value(general_info, "Year built")),
        "living_area": first_value(general_info, "Living area"),
        "building_size": first_value(general_info, "Building size"),
        "lot_size": first_value(general_info, "Lot size"),
        "lot_area": first_value(general_info, "Lot area"),
        "municipality_tax": first_value(key_features_by_key, "municipality_tax"),
        "school_tax": first_value(key_features_by_key, "school_tax"),
        "condo_fees": first_value(key_features_by_key, "condo_fees") or first_value(general_info, "Condo fees"),
        "description": narrative.get("description"),
        "addendums": narrative.get("addendums"),
        "inclusions": narrative.get("inclusions"),
        "exclusions": narrative.get("exclusions"),
        "geocode_address": extract_geocode_address(soup),
        "nearby_json": dumps(nearby),
        "key_features_json": dumps(key_feature_rows),
        "details_json": json.dumps(details_by_section, ensure_ascii=False, sort_keys=True),
        "features_json": dumps(feature_rows),
        "sections_json": dumps(narrative_rows),
        "brokers_json": dumps(extract_brokers(soup)),
        "image_urls_json": dumps(extract_images(soup)),
        "raw_text": clean_text(soup.select_one(".page-property-details")) or clean_text(soup.body),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    return data


def setup_database(db_name=DB_PATH):
    print(f"[DB] Initializing database: {db_name}")
    Path(db_name).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            centris_id TEXT PRIMARY KEY,
            url TEXT,
            address TEXT,
            street_address TEXT,
            city_region TEXT,
            postal_code TEXT,
            province TEXT,
            listing_type TEXT,
            transaction_type TEXT,
            status TEXT,
            price TEXT,
            price_amount REAL,
            bedrooms INTEGER,
            bathrooms INTEGER,
            powder_rooms INTEGER,
            property_type TEXT,
            building_type TEXT,
            year_built INTEGER,
            living_area TEXT,
            building_size TEXT,
            lot_size TEXT,
            lot_area TEXT,
            municipality_tax TEXT,
            school_tax TEXT,
            condo_fees TEXT,
            description TEXT,
            addendums TEXT,
            inclusions TEXT,
            exclusions TEXT,
            geocode_address TEXT,
            nearby_json TEXT,
            key_features_json TEXT,
            details_json TEXT,
            features_json TEXT,
            sections_json TEXT,
            brokers_json TEXT,
            image_urls_json TEXT,
            raw_text TEXT,
            scraped_at TEXT
        )
    """)
    ensure_property_columns(cursor)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS brokers (
            broker_id TEXT PRIMARY KEY,
            name TEXT,
            title TEXT,
            phone TEXT,
            email TEXT,
            profile_url TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS property_brokers (
            centris_id TEXT,
            broker_id TEXT,
            sort_order INTEGER,
            PRIMARY KEY (centris_id, broker_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS property_sections (
            centris_id TEXT,
            section_key TEXT,
            title TEXT,
            body TEXT,
            PRIMARY KEY (centris_id, section_key)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS property_feature_values (
            centris_id TEXT,
            section_title TEXT,
            label TEXT,
            key TEXT,
            value TEXT,
            sort_order INTEGER,
            PRIMARY KEY (centris_id, section_title, label, value, sort_order)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS property_images (
            centris_id TEXT,
            image_url TEXT,
            sort_order INTEGER,
            PRIMARY KEY (centris_id, image_url)
        )
    """)
    conn.commit()
    return conn


def ensure_property_columns(cursor):
    cursor.execute("PRAGMA table_info(properties)")
    existing = {row[1] for row in cursor.fetchall()}
    column_types = {
        "price_amount": "REAL",
        "bedrooms": "INTEGER",
        "bathrooms": "INTEGER",
        "powder_rooms": "INTEGER",
        "year_built": "INTEGER",
    }
    for column in PROPERTY_COLUMNS:
        if column not in existing:
            cursor.execute(f"ALTER TABLE properties ADD COLUMN {column} {column_types.get(column, 'TEXT')}")


def get_property_ids(driver, start_page=1, max_pages=3):
    property_ids = set()
    base_search_url = f"{BASE_URL}/property/search.php"

    for page in range(start_page, start_page + max_pages):
        url = (
            f"{base_search_url}?district=any&prop_type=any&broker=any&project=any"
            f"&price_range=any&sales=1&rentals=1&subSearch=submit&page={page}&sort=recent"
        )
        print(f"[SEARCH] Loading search page {page}: {url}")

        driver.get(url)
        time.sleep(3)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        items = soup.find_all("a", class_="project-item")

        if not items:
            print(f"[SEARCH] No property items found on page {page}. Halting search.")
            break

        print(f"[SEARCH] Found {len(items)} property elements on page {page}.")

        for item in items:
            href = item.get("href", "")
            match = re.search(r"[?&]id=(\d+)", href) or re.search(r"CENTRIS #:\s*(\d+)", item.get_text(" ", strip=True))
            if match:
                centris_id = match.group(1)
                property_ids.add(centris_id)
                print(f"[SEARCH] Extracted ID: {centris_id}")
            else:
                print("[SEARCH] Warning: Failed to parse Centris ID from search item.")

    return sorted(property_ids)


def parse_property_page(driver, centris_id):
    url = f"{BASE_URL}/property/viewProperty.php?id={centris_id}"
    print(f"[PARSE] Loading property page for ID {centris_id}: {url}")

    driver.get(url)
    time.sleep(2)

    data = parse_property_html(driver.page_source, centris_id, url)
    if data:
        print(
            f"[PARSE] Extracted {centris_id} | {data.get('transaction_type')} | "
            f"{data.get('price')} | {data.get('property_type')} | "
            f"{len(json.loads(data.get('brokers_json') or '[]'))} brokers"
        )
    return data


def save_property(cursor, data):
    values = [data.get(column) for column in PROPERTY_COLUMNS]
    placeholders = ", ".join("?" for _ in PROPERTY_COLUMNS)
    columns = ", ".join(PROPERTY_COLUMNS)
    updates = ", ".join(f"{column}=excluded.{column}" for column in PROPERTY_COLUMNS if column != "centris_id")

    cursor.execute(
        f"""
        INSERT INTO properties ({columns})
        VALUES ({placeholders})
        ON CONFLICT(centris_id) DO UPDATE SET {updates}
        """,
        values,
    )

    centris_id = data["centris_id"]
    cursor.execute("DELETE FROM property_brokers WHERE centris_id = ?", (centris_id,))
    cursor.execute("DELETE FROM property_sections WHERE centris_id = ?", (centris_id,))
    cursor.execute("DELETE FROM property_feature_values WHERE centris_id = ?", (centris_id,))
    cursor.execute("DELETE FROM property_images WHERE centris_id = ?", (centris_id,))

    brokers = json.loads(data.get("brokers_json") or "[]")
    for index, broker in enumerate(brokers):
        broker_id = broker.get("broker_id") or broker.get("email") or broker.get("name")
        if not broker_id:
            continue
        cursor.execute(
            """
            INSERT INTO brokers (broker_id, name, title, phone, email, profile_url)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(broker_id) DO UPDATE SET
                name=excluded.name,
                title=excluded.title,
                phone=excluded.phone,
                email=excluded.email,
                profile_url=excluded.profile_url
            """,
            (broker_id, broker.get("name"), broker.get("title"), broker.get("phone"), broker.get("email"), broker.get("profile_url")),
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO property_brokers (centris_id, broker_id, sort_order)
            VALUES (?, ?, ?)
            """,
            (centris_id, broker_id, index),
        )

    for section in json.loads(data.get("sections_json") or "[]"):
        cursor.execute(
            """
            INSERT OR REPLACE INTO property_sections (centris_id, section_key, title, body)
            VALUES (?, ?, ?, ?)
            """,
            (centris_id, section.get("key"), section.get("title"), section.get("body")),
        )

    detail_sections = json.loads(data.get("details_json") or "{}")
    sort_order = 0
    for section_title, grouped in detail_sections.items():
        for label, values_for_label in grouped.items():
            for value in values_for_label:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO property_feature_values
                    (centris_id, section_title, label, key, value, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (centris_id, section_title, label, normalize_label(label), value, sort_order),
                )
                sort_order += 1

    for index, image_url in enumerate(json.loads(data.get("image_urls_json") or "[]")):
        cursor.execute(
            """
            INSERT OR REPLACE INTO property_images (centris_id, image_url, sort_order)
            VALUES (?, ?, ?)
            """,
            (centris_id, image_url, index),
        )


def create_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1400,2200")

    chrome_service = Service(executable_path="/snap/bin/chromium.chromedriver")
    return webdriver.Chrome(service=chrome_service, options=options)


def main():
    print("[SYSTEM] Starting scraper script.")
    conn = setup_database()
    cursor = conn.cursor()

    print("[SYSTEM] Launching Chrome browser.")
    driver = create_driver()

    try:
        centris_ids = get_property_ids(driver, start_page=3, max_pages=28)
        print(f"[SYSTEM] Total unique properties identified: {len(centris_ids)}")

        for cid in centris_ids:
            property_data = parse_property_page(driver, cid)

            if property_data:
                print(f"[DB] Saving ID {cid} to database.")
                save_property(cursor, property_data)
                conn.commit()
            else:
                print(f"[DB] Skipping ID {cid} due to parsing failure.")

            time.sleep(1)

    except Exception as e:
        print(f"[SYSTEM] An unexpected error occurred: {e}")
        raise

    finally:
        print("[SYSTEM] Closing browser and database connection.")
        driver.quit()
        conn.close()


if __name__ == "__main__":
    main()
