from bs4 import BeautifulSoup
import aiohttp
import asyncio
import os
import random

# --- Configuration ---
MAX_CONCURRENT_REQUESTS = 5      # Max parallel connections at any time
DELAY_BETWEEN_REQUESTS = (0.5, 1.5)  # Random delay range (seconds) between requests
MAX_RETRIES = 5                  # Number of retries on failure
INITIAL_BACKOFF = 2              # Initial backoff in seconds (doubles each retry)
REQUEST_TIMEOUT = 30             # Timeout per request in seconds

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def safe_mkdir(directory):
    os.makedirs(directory, exist_ok=True)


async def throttled_request(semaphore, coro):
    """Wrap a coroutine with a semaphore and a random delay for rate limiting."""
    async with semaphore:
        await asyncio.sleep(random.uniform(*DELAY_BETWEEN_REQUESTS))
        return await coro


async def fetch(session, url, semaphore, retries=MAX_RETRIES):
    """Fetch a URL with retry + exponential backoff."""
    backoff = INITIAL_BACKOFF
    for attempt in range(1, retries + 1):
        try:
            print(f"Fetching URL: {url}")
            async with semaphore:
                await asyncio.sleep(random.uniform(*DELAY_BETWEEN_REQUESTS))
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as response:
                    return await response.text()
        except Exception as e:
            print(f"  Attempt {attempt}/{retries} failed for {url}: {e}")
            if attempt < retries:
                jitter = random.uniform(0, backoff * 0.5)
                wait = backoff + jitter
                print(f"  Retrying in {wait:.1f}s...")
                await asyncio.sleep(wait)
                backoff *= 2
            else:
                print(f"  All {retries} attempts failed for {url}. Skipping.")
                return None


async def download_image(session, url, path_to_file, semaphore, retries=MAX_RETRIES):
    """Download an image with retry + exponential backoff."""
    backoff = INITIAL_BACKOFF
    for attempt in range(1, retries + 1):
        try:
            print(f"\tDownloading image from URL: {url}")
            async with semaphore:
                await asyncio.sleep(random.uniform(*DELAY_BETWEEN_REQUESTS))
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as response:
                    if response.status == 200:
                        with open(path_to_file, 'wb') as f:
                            while True:
                                chunk = await response.content.read(1024)
                                if not chunk:
                                    break
                                f.write(chunk)
                        print(f"\t\tImage saved to: {path_to_file}")
                        return True
                    else:
                        print(f"\t\tFailed to download {url}. Status code: {response.status}")
                        return False
        except Exception as e:
            print(f"\t\tAttempt {attempt}/{retries} failed for {url}: {e}")
            if attempt < retries:
                jitter = random.uniform(0, backoff * 0.5)
                wait = backoff + jitter
                print(f"\t\tRetrying in {wait:.1f}s...")
                await asyncio.sleep(wait)
                backoff *= 2
            else:
                print(f"\t\tAll {retries} attempts failed for {url}. Skipping.")
                return False


async def parse_navbar(session, url, skins_dir, semaphore):
    print(f"\tParsing navbar: {url}")
    html = await fetch(session, url, semaphore)
    if html is None:
        return
    soup = BeautifulSoup(html, 'lxml')

    navbar = soup.find('nav', class_='main')
    sections = navbar.find_all('li')[:-1]  # Skip the last item

    # Process sections sequentially to be gentler on the server
    for li in sections:
        await parse_section(session, url, li.a['href'], skins_dir, semaphore)


async def get_num_pages(session, section_url, semaphore):
    html = await fetch(session, section_url, semaphore)
    if html is None:
        return 0
    soup = BeautifulSoup(html, 'lxml')

    page_counter = soup.find('span', class_='count')
    page_counter_span = page_counter.find('span')
    page_counter_str = page_counter_span.text
    page_count_start = page_counter_str.rfind(' ') + 1
    return int(page_counter_str[page_count_start:])


async def parse_section(session, base_url, section, skins_dir, semaphore):
    section_dir = os.path.join(skins_dir, os.path.basename(section))
    safe_mkdir(section_dir)

    section_url = base_url + section
    num_pages = await get_num_pages(session, section_url, semaphore)

    for i in range(1, num_pages + 1):
        section_page_url = f"{section_url}/{i}"
        html = await fetch(session, section_page_url, semaphore)
        if html is None:
            continue
        soup = BeautifulSoup(html, 'lxml')

        skin_blocks = soup.find_all('div', class_='card')

        # Process skins on this page with limited concurrency
        tasks = []
        for block in skin_blocks:
            skin = block.find('a')['href']
            tasks.append(process_skin(session, base_url, skin, section_dir, semaphore))

        # Run skin downloads in batches to avoid overwhelming the server
        await asyncio.gather(*tasks)


async def process_skin(session, base_url, skin, section_dir, semaphore):
    """Process a single skin: fetch metadata and download the image."""
    skin_dir = os.path.join(section_dir, os.path.basename(skin))
    safe_mkdir(skin_dir)

    skin_url = base_url + skin
    skin_result = await fetch(session, skin_url, semaphore)
    if skin_result is None:
        return

    # Get the name of the skin
    skin_name = skin_result[skin_result.find("<h2 class=\"card-title\">") + 23:]
    skin_name = skin_name[:skin_name.find('<')]

    # Get the description for the skin
    skin_description = skin_result[skin_result.find("<p class=\"card-description\">") + 28:]
    skin_description = skin_description[:skin_description.find('<')]

    # Create a text file containing the skin's name and description
    with open(os.path.join(skin_dir, "meta.txt"), 'w') as f:
        f.write(f"Name: {skin_name}\nDescription: {skin_description}")

    # Download the skin image
    skin_img_url = skin_url + "/download"
    path_to_file = os.path.join(skin_dir, "skin.png")
    await download_image(session, skin_img_url, path_to_file, semaphore)


async def main():
    print("Starting the script.")
    skins_dir = "skins"
    safe_mkdir(skins_dir)

    url = "https://www.minecraftskins.net"

    # Limit total concurrent TCP connections
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_REQUESTS, force_close=True)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
        await parse_navbar(session, url, skins_dir, semaphore)

    print("Done!")


# Run the main coroutine
try:
    asyncio.run(main())
except Exception as e:
    print(f"An error occurred: {e}")
