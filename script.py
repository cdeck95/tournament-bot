import os
import discord
from discord.ext import tasks
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timedelta
import boto3
from botocore.exceptions import ClientError
import logging
import sys
from pytz import timezone
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time
import random
import asyncio
import concurrent.futures
from detail_worker import DetailWorker
import platform
import subprocess

# Add these rate limiting constants
REQUEST_COOLDOWN_MIN = 1  # Reduced minimum delay to avoid heartbeat timeouts
REQUEST_COOLDOWN_MAX = 3  # Reduced maximum delay to avoid heartbeat timeouts 
PAGE_LOAD_WAIT = 3        # Reduced wait time to avoid heartbeat timeouts
MAX_PAGINATION_PAGES = 2  # Maximum number of "load more" pages to request

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Set the minimum log level to INFO
    format="%(asctime)s - %(levelname)s - %(message)s",  # Log format with timestamp
    handlers=[
        logging.StreamHandler(sys.stdout),  # Send logs to standard output
        logging.FileHandler("bot.log", encoding="utf-8")  # Optional: Save logs to a file
    ]
)

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID'))  # Discord channel ID as integer

# AWS S3 Config
S3_BUCKET_NAME = "discord-bot-public-files"
S3_FILE_KEY = "tournaments.json"

# Initialize S3 client
s3 = boto3.client('s3')

# Discord client
intents = discord.Intents.default()
client = discord.Client(intents=intents)

TOURNAMENTS_FILE = "tournaments.json"
TOURNAMENT_SEARCH_URL = "https://www.discgolfscene.com/tournaments/search"
ZIP_CODE = "08043"  # Echelon, NJ
SEARCH_DISTANCE = "70"  # miles

def setup_webdriver():
    """Set up and return a headless Chrome webdriver for web scraping"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")  # Updated headless mode syntax
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")
    
    # Environment detection for browser binary
    is_render = "RENDER" in os.environ
    is_linux = platform.system() == "Linux"
    
    # For Linux environments like Render, we need to find or install Chrome
    if is_linux:
        # Try to find Chrome or Chromium
        chrome_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
        ]
        
        chrome_found = False
        for chrome_path in chrome_paths:
            if os.path.exists(chrome_path):
                chrome_options.binary_location = chrome_path
                logging.info(f"Using Chrome/Chromium at: {chrome_path}")
                chrome_found = True
                break
                
        if not chrome_found and is_render:
            logging.info("Chrome not found, attempting to install Chromium on Render...")
            try:
                # Install Chromium on Render
                subprocess.run(["apt-get", "update"], check=True)
                subprocess.run(["apt-get", "install", "-y", "chromium-browser"], check=True)
                chrome_options.binary_location = "/usr/bin/chromium-browser"
                logging.info("Chromium installed successfully")
            except Exception as e:
                logging.error(f"Failed to install Chromium: {e}")
                return None
    
    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), 
            options=chrome_options
        )
        return driver
    except Exception as e:
        logging.error(f"Failed to create webdriver: {e}")
        logging.info("Falling back to direct HTML scraping method...")
        return None

def load_tournaments_from_s3():
    """Load tournaments list from S3"""
    try:
        response = s3.get_object(Bucket=S3_BUCKET_NAME, Key=S3_FILE_KEY)
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            logging.error("No tournaments file found in S3. Initializing empty list.")
            return []  # If file doesn't exist, return an empty list
        else:
            logging.error(f"Error accessing S3: {e}")
            return []

# Use an executor for running synchronous code in background without blocking Discord
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)  # Increased to 2 workers

async def fetch_tournaments_async():
    """Async wrapper for fetch_tournaments to avoid blocking Discord heartbeat"""
    loop = asyncio.get_running_loop()
    try:
        # First try using Selenium
        tournaments = await loop.run_in_executor(thread_pool, fetch_tournaments)
        if tournaments:
            return tournaments
            
        # If Selenium failed or returned no tournaments, try fallback method
        logging.info("Switching to fallback HTML scraping method")
        return await loop.run_in_executor(thread_pool, fetch_tournaments_fallback)
    except Exception as e:
        logging.error(f"Error fetching tournaments: {e}")
        return []

def fetch_tournaments_fallback():
    """
    Fallback method to fetch tournaments using direct HTTP requests instead of Selenium.
    This is used when the webdriver setup fails.
    """
    logging.info("Using fallback tournament fetch method")
    tournaments = []
    
    try:
        # First make a request to the search page to get any necessary cookies
        session = requests.Session()
        
        # Add headers to mimic a browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0',
        }
        
        # First get the search page
        logging.info("Accessing tournament search page")
        search_page = session.get(TOURNAMENT_SEARCH_URL, headers=headers)
        
        # Now access the filter page
        logging.info("Accessing filter page")
        filter_url = "https://www.discgolfscene.com/tournaments/search-filter"
        filter_page = session.get(filter_url, headers=headers)
        
        # Now submit the search form with our parameters
        logging.info("Submitting search form")
        form_data = {
            'filter_tournaments_country': 'USA',
            'filter_usa_state': '',  # Any state
            'filter_location_name': ZIP_CODE,
            'filter_location_zip': ZIP_CODE,
            'filter_location_latitude': '39.846520',  # Echelon, NJ coordinates
            'filter_location_longitude': '-74.960981',
            'filter_location_distance': SEARCH_DISTANCE,
            'date_range': '0',  # All upcoming
            'tournament_formats[]': '',  # Any format
            'types[]': '',  # Any event type
        }
        
        response = session.post(filter_url, data=form_data, headers=headers)
        
        if response.status_code == 200:
            logging.info("Search form submitted successfully")
            # Parse the initial page of results
            initial_tournaments = parse_tournament_page(response.text)
            tournaments.extend(initial_tournaments)
            
            # Now try to load more results
            for page in range(1, MAX_PAGINATION_PAGES + 1):
                logging.info(f"Fetching additional page {page} of tournaments")
                more_url = f"https://www.discgolfscene.com/tournaments/search-results?limit=50,{50*page}"
                
                # Add a small delay to avoid overwhelming the server
                time.sleep(random.uniform(1.5, 3.0))
                
                more_response = session.get(more_url, headers=headers)
                if more_response.status_code == 200:
                    more_tournaments = parse_tournament_page(more_response.text, len(tournaments))
                    if not more_tournaments:
                        break
                    tournaments.extend(more_tournaments)
                else:
                    logging.warning(f"Failed to load more tournaments: {more_response.status_code}")
                    break
        else:
            logging.error(f"Search form submission failed: {response.status_code}")
            
        logging.info(f"Found {len(tournaments)} tournaments with fallback method")
        return tournaments
    
    except Exception as e:
        logging.error(f"Error in fallback tournament fetch: {e}")
        return []

def fetch_tournaments():
    """Fetch tournaments from the website using Selenium to interact with search filters"""
    try:
        driver = setup_webdriver()
        if not driver:
            logging.error("Could not initialize webdriver. Aborting tournament fetch.")
            return []
        
        logging.info(f"Loading tournament search page: {TOURNAMENT_SEARCH_URL}")
        driver.get(TOURNAMENT_SEARCH_URL)
        
        # Add humanized waiting period after page load - use shorter delays
        time.sleep(random.uniform(1, 2))
        
        # Wait for the page to load - looking for the search form instead
        WebDriverWait(driver, PAGE_LOAD_WAIT).until(
            EC.presence_of_element_located((By.CLASS_NAME, "category-search"))
        )
        
        # Take a screenshot for debugging
        driver.save_screenshot("search_page.png")
        logging.info("Page screenshot saved as search_page.png")
        
        # Check for and dismiss the classic version banner if present
        try:
            banner = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.ID, "desktop-sunset"))
            )
            logging.info("Classic version banner found, attempting to dismiss it")
            
            # Short delay before interaction (more human-like)
            time.sleep(random.uniform(1, 2))
            
            # Try to find and click the "Ok" button
            ok_button = banner.find_element(By.CSS_SELECTOR, "a.btn.btn-primary")
            driver.execute_script("arguments[0].click();", ok_button)
            logging.info("Clicked 'Ok' button on classic version banner")
            
            # Wait a moment for the banner to be removed
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            # Banner not found or couldn't be dismissed
            logging.info(f"Classic version banner not found or couldn't be dismissed: {e}")
        
        # Click on the filter link to open the filter page
        try:
            # Take a screenshot to check that banner is gone
            driver.save_screenshot("after_banner_dismissed.png")
            
            # Add reasonable delay before clicking filter link
            time.sleep(random.uniform(REQUEST_COOLDOWN_MIN, REQUEST_COOLDOWN_MAX))
            
            # Try to find and click the filter link with multiple methods
            try:
                filter_link = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, "search-filter-anchor"))
                )
                # Try JavaScript click first (more reliable with overlays)
                driver.execute_script("arguments[0].click();", filter_link)
                logging.info("Clicked on filter link using JavaScript")
                
                # Wait longer for the page to load (simulates human browsing)
                time.sleep(PAGE_LOAD_WAIT)
                
            except Exception as e:
                logging.warning(f"JavaScript click failed: {e}")
                
                # Try alternate method with regular click
                filter_link = driver.find_element(By.CLASS_NAME, "search-filter-anchor")
                filter_link.click()
                logging.info("Clicked on filter link using regular click")
                
                # Wait longer for the page to load
                time.sleep(PAGE_LOAD_WAIT)
                
        except Exception as e:
            logging.error(f"Failed to click filter link: {e}")
            # Try direct navigation instead
            driver.get("https://www.discgolfscene.com/tournaments/search-filter")
            logging.info("Navigated directly to filter page")
            time.sleep(2) # Reduced delay
        
        # Wait for the filter form to load
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "search-filter"))
            )
            
            # Take a screenshot of the filter form
            driver.save_screenshot("filter_form.png")
            logging.info("Filter form screenshot saved as filter_form.png")
            
            # Step 1: First make sure USA is selected in the country dropdown
            try:
                # Add a short delay before interacting with the form (more human-like but shorter)
                time.sleep(random.uniform(0.5, 1))
                
                # Select the Country dropdown
                country_select = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "filter_tournaments_country"))
                )
                
                # Use JavaScript to set value to USA
                driver.execute_script("arguments[0].value = 'USA';", country_select)
                # Trigger change event to show state fields
                driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", country_select)
                logging.info("Set country to USA")
                
                # Give the page a moment to update the form based on country selection
                time.sleep(random.uniform(1, 2))
                
                # Take a screenshot after country selection
                driver.save_screenshot("after_country_selection.png")
                logging.info("Country selection screenshot saved")
                
                # Add another pause before state selection
                time.sleep(random.uniform(1, 1.5))
                
                # Make sure "Any" is selected for state (default option)
                state_select = driver.find_element(By.ID, "filter_usa_state")
                driver.execute_script("arguments[0].value = '';", state_select)
                driver.execute_script("arguments[0].dispatchEvent(new Event('change'));", state_select)
                logging.info("Set state to Any")
            except Exception as e:
                logging.error(f"Failed to set country/state: {e}")
            
            # Step 2: Now handle the location section which should be visible since USA is selected
            try:
                # Add a short delay before location form interactions
                time.sleep(random.uniform(1, 2))
                
                # Check if we need to show the location input first
                location_display = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.ID, "location-display"))
                )
                
                if location_display.is_displayed():
                    # Click the change location link to show the input field
                    change_location = location_display.find_element(By.CSS_SELECTOR, "a")
                    driver.execute_script("arguments[0].click();", change_location)
                    logging.info("Clicked 'Change location' link")
                    time.sleep(random.uniform(1, 2))  # Wait for the location field to appear
                
                # Now find and set the location name/ZIP code
                location_name = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "filter_location_name"))
                )
                location_name.clear()
                
                # Type the ZIP code more like a human (character by character with slight delays)
                for digit in ZIP_CODE:
                    location_name.send_keys(digit)
                    time.sleep(random.uniform(0.1, 0.3))  # Small delay between keystrokes
                logging.info(f"Set location/ZIP to {ZIP_CODE}")
                
                # Wait for any autocomplete to process (more human-like behavior)
                time.sleep(random.uniform(1.5, 2.5))
                
                # Set the hidden ZIP code field as well
                driver.execute_script(f"document.getElementById('filter_location_zip').value = '{ZIP_CODE}';")
                
                # Set the latitude and longitude fields (these are required for the search to work correctly)
                driver.execute_script("document.getElementById('filter_location_latitude').value = '39.846520';")
                driver.execute_script("document.getElementById('filter_location_longitude').value = '-74.960981';")
                logging.info("Set location coordinates for Echelon, NJ")
                
                # Add a short pause before setting distance
                time.sleep(random.uniform(1, 2))
                
                # Step 3: Set the distance
                distance_input = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "filter_location_distance"))
                )
                distance_input.clear()
                
                # Type the distance more like a human (character by character with slight delays)
                for digit in SEARCH_DISTANCE:
                    distance_input.send_keys(digit)
                    time.sleep(random.uniform(0.1, 0.3))  # Small delay between keystrokes
                logging.info(f"Set distance to {SEARCH_DISTANCE} miles")
                
                # Take a screenshot of the completed form
                driver.save_screenshot("completed_form.png")
                logging.info("Screenshot of completed form saved")
            except Exception as e:
                logging.error(f"Failed to set location/distance: {e}")
            
            # Make sure the "All upcoming" date range is selected
            try:
                # Add a short delay
                time.sleep(random.uniform(1, 1.5))
                
                date_range_all = driver.find_element(By.ID, "date-range-0")
                if not date_range_all.is_selected():
                    driver.execute_script("arguments[0].click();", date_range_all)
                    logging.info("Selected 'All upcoming' date range")
            except Exception as e:
                logging.warning(f"Could not set date range: {e}")
            
            # Add another short delay before submitting (human-like pause before form submission)
            time.sleep(random.uniform(0.5, 1))
            
            # Take a screenshot before submitting the form
            driver.save_screenshot("before_submit.png")
            logging.info("Form filled. Screenshot saved as before_submit.png")
            
            # Submit the form by clicking the search button
            try:
                submit_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, ".search-filter .submit-buttons input[type='submit']"))
                )
                driver.execute_script("arguments[0].click();", submit_btn)
                logging.info("Clicked submit button using JavaScript")
            except Exception as e:
                logging.error(f"Failed to click submit button: {e}")
                try:
                    # Try submitting the form directly
                    form = driver.find_element(By.CSS_SELECTOR, "form.search-filter")
                    driver.execute_script("arguments[0].submit();", form)
                    logging.info("Submitted form using JavaScript")
                except Exception as js_e:
                    logging.error(f"Failed to submit form: {js_e}")
            
            # Wait longer for the results page to load - websites often delay search results intentionally
            # to prevent scraping
            time.sleep(PAGE_LOAD_WAIT * 1.5)
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.ID, "tournaments-list"))
                )
                logging.info("Results page loaded successfully")
            except Exception as e:
                logging.error(f"Timed out waiting for results page: {e}")
                # Continue anyway as the page might still have loaded partially
            
        except Exception as e:
            logging.error(f"Error interacting with filter form: {e}")
            # Continue anyway to see if we can parse tournaments from current page
        
        # Take a screenshot of results
        driver.save_screenshot("results_page.png")
        logging.info("Results page screenshot saved as results_page.png")
        
        # Parse the results
        tournaments = []
        
        # Get the initial page of results
        page_html = driver.page_source
        initial_tournaments = parse_tournament_page(page_html)
        logging.info(f"Initially found {len(initial_tournaments)} tournaments")
        tournaments.extend(initial_tournaments)
        
        # Check if there are more pages to load
        try:
            # Look for "Load more tournaments..." link at the bottom
            load_more_container = driver.find_element(By.ID, "load-tournaments-50-50")
            if load_more_container:
                load_more_link = load_more_container.find_element(By.CSS_SELECTOR, "a.load-more")
                
                # Limit the number of pages we load to avoid rate limiting
                page_count = 1
                
                while (load_more_link and load_more_link.is_displayed() and 
                       page_count < MAX_PAGINATION_PAGES):
                    logging.info(f"Found 'load more' link, clicking (page {page_count+1} of max {MAX_PAGINATION_PAGES})...")
                    
                    # Add a shorter delay between pagination requests to avoid heartbeat timeouts
                    wait_time = random.uniform(1, 2)
                    logging.info(f"Waiting {wait_time:.2f} seconds before loading more tournaments...")
                    time.sleep(wait_time)
                    
                    # Use JavaScript click for better reliability
                    driver.execute_script("arguments[0].click();", load_more_link)
                    
                    # Wait longer for new content to load
                    time.sleep(PAGE_LOAD_WAIT)
                    
                    # Parse the newly loaded tournaments
                    new_html = driver.page_source
                    new_tournaments = parse_tournament_page(new_html, len(tournaments))
                    
                    if not new_tournaments:
                        logging.info("No new tournaments found, stopping pagination")
                        break
                    
                    logging.info(f"Found {len(new_tournaments)} additional tournaments")
                    tournaments.extend(new_tournaments)
                    page_count += 1
                    
                    # Re-find the load-more link (it might have been replaced)
                    try:
                        # The container might be gone after clicking
                        load_more_container = driver.find_element(By.CSS_SELECTOR, "[id^='load-tournaments-']")
                        if load_more_container:
                            load_more_link = load_more_container.find_element(By.CSS_SELECTOR, "a.load-more")
                        else:
                            break
                    except:
                        logging.info("No more 'load more' links found")
                        break
                
                if page_count >= MAX_PAGINATION_PAGES:
                    logging.info(f"Reached maximum page limit ({MAX_PAGINATION_PAGES}). Stopping pagination to avoid rate limiting.")
        except Exception as e:
            logging.info(f"No 'load more' link or error occurred: {e}")
        
        logging.info(f"Found {len(tournaments)} tournaments total")
        driver.quit()
        return tournaments
        
    except Exception as e:
        logging.error(f"Error fetching tournaments: {e}")
        if 'driver' in locals() and driver:
            try:
                driver.save_screenshot("error_state.png")
                logging.info("Error state screenshot saved as error_state.png")
                driver.quit()
            except:
                pass
        return []

def parse_tournament_page(html_content, existing_count=0):
    """Parse the tournament listings from the HTML content"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    tournaments = []
    tournament_divs = soup.select(".tournament-list.list-record")
    
    # Skip already processed tournaments if we're loading more
    tournament_divs = tournament_divs[existing_count:] if existing_count > 0 else tournament_divs
    
    # Current date for year handling
    now = datetime.now()
    
    logging.info(f"Parsing {len(tournament_divs)} tournament entries")

    for div in tournament_divs:
        try:
            # Extract URL from the <a> tag
            link_tag = div.select_one("a")
            url = link_tag['href'] if link_tag and link_tag.has_attr('href') else "N/A"
            
            # Extract name
            name_span = div.select_one("span.name")
            name = name_span.text.strip() if name_span else "N/A"
            
            # Extract info spans for date, tier, etc.
            info_spans = div.select("span.info")
            
            # Extract tier and date from the first info span
            date_text = ""
            tier = None
            
            if info_spans and len(info_spans) > 0:
                info_text = info_spans[0].text.strip()
                
                # Check if it contains a tier
                if "PDGA" in info_text:
                    tier_parts = info_text.split("·")
                    tier = tier_parts[0].strip()
                    if len(tier_parts) > 1:
                        date_text = tier_parts[1].strip()
                elif "Disc Golf Pro Tour" in info_text:
                    tier = "Disc Golf Pro Tour"
                    date_parts = info_text.split("·") if "·" in info_text else info_text.split(tier)
                    if len(date_parts) > 1:
                        date_text = date_parts[1].strip()
                    else:
                        date_text = info_text.replace(tier, "").strip()
                else:
                    date_text = info_text.strip()
            
            # Parse date_text to get a standard format
            date = "N/A"
            try:
                # Handle different date formats
                if "-" in date_text:  # Format like "Sat-Sun, Mar 22-23, 2025"
                    date_parts = date_text.split(",")
                    if len(date_parts) >= 2:
                        month_day_year = date_parts[-1].strip()
                        if "20" in month_day_year:  # Contains year
                            # Try different formats
                            try:
                                date_obj = datetime.strptime(month_day_year, "%b %d, %Y")
                                date = date_obj.strftime("%m/%d/%Y")
                            except ValueError:
                                # Try another format (handle cases like "Mar 22-23, 2025")
                                # Extract just the month and year
                                month = month_day_year.split(" ")[0]
                                year = month_day_year.split(" ")[-1]
                                # Use the first day of the month as an approximation
                                try:
                                    date_obj = datetime.strptime(f"{month} 1, {year}", "%b %d, %Y")
                                    date = date_obj.strftime("%m/%d/%Y")
                                except ValueError:
                                    date = "N/A"
                elif "," in date_text:  # Format like "Sat, Mar 15, 2025"
                    date_parts = date_text.split(",")
                    if len(date_parts) >= 2:
                        month_day_year = ",".join(date_parts[1:]).strip()
                        try:
                            date_obj = datetime.strptime(month_day_year, " %b %d, %Y")
                            date = date_obj.strftime("%m/%d/%Y")
                        except ValueError:
                            # Try alternative format
                            try:
                                date_obj = datetime.strptime(month_day_year, " %B %d, %Y")
                                date = date_obj.strftime("%m/%d/%Y")
                            except ValueError:
                                date = "N/A"
            except Exception as e:
                logging.warning(f"Failed to parse date from '{date_text}': {e}")
                date = "N/A"
            
            # Extract location and registrants from the second info span
            location = "N/A"
            registrants = 0
            capacity = 0
            
            if info_spans and len(info_spans) > 1:
                location_info = info_spans[1]
                location_span = location_info.select_one("span")
                if location_span:
                    location = location_span.text.strip()
                
                # Look for registration numbers which are in format "##" or "## / ##"
                user_group_icon = location_info.select_one("i.fa-user-group")
                
                if user_group_icon:
                    # Find the <b> tag that follows the user-group icon
                    reg_numbers_b = user_group_icon.find_next("b")
                    if reg_numbers_b:
                        reg_numbers = reg_numbers_b.text.strip()
                        if "/" in reg_numbers:
                            reg_parts = reg_numbers.split("/")
                            try:
                                registrants = int(reg_parts[0].strip())
                                capacity = int(reg_parts[1].strip())
                            except ValueError:
                                registrants = 0
                                capacity = 0
                        else:
                            try:
                                registrants = int(reg_numbers.strip())
                            except ValueError:
                                registrants = 0
            
            # Check if registration is open - tournaments with upcoming registration will have a timestamp
            registration_open = True  # Default to assume open
            if info_spans and len(info_spans) > 2:
                reg_info = info_spans[2].text.strip()
                if "at" in reg_info and ("EDT" in reg_info or "EST" in reg_info):  # Registration opens in the future
                    registration_open = False
            
            tournaments.append({
                "name": name,
                "url": url,
                "registration_open": registration_open,
                "location": location,
                "date": date,
                "registrants": registrants,
                "capacity": capacity,
                "tier": tier
            })
        except Exception as e:
            logging.error(f"Error parsing tournament entry: {e}")
            continue

    return tournaments

def save_tournaments_to_s3(tournaments):
    """Save tournaments to S3 bucket"""
    if not tournaments:
        logging.error("No tournaments to save. Skipping S3 upload.")
        return False

    try:
        # Custom serialization for datetime objects
        def serialize(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()  # Convert datetime to ISO 8601 string
            raise TypeError(f"Type {type(obj)} not serializable")

        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=S3_FILE_KEY,
            Body=json.dumps(tournaments, indent=4, default=serialize),  # Use custom serializer
            ContentType="application/json"
        )
        return True
    except ClientError as e:
        logging.error(f'Error saving tournaments to S3: {e}')
        return False

async def save_tournaments_async(tournaments):
    """Async wrapper for save_tournaments to avoid blocking Discord"""
    loop = asyncio.get_running_loop()
    saved_tournaments = await loop.run_in_executor(thread_pool, load_tournaments_from_s3)
    logging.info(f"Loaded {len(saved_tournaments)} saved tournaments")

    # Identify new tournaments (unique by name, date, and location)
    new_tournaments = [
        t for t in tournaments if not any(
            t["name"] == saved["name"] and
            t["date"] == saved["date"] and
            t["location"] == saved["location"]
            for saved in saved_tournaments
        )
    ]

    # Initialize flags for new tournaments
    for tournament in tournaments:
        matching_saved = next(
            (saved for saved in saved_tournaments if 
             saved["name"] == tournament["name"] and 
             saved["date"] == tournament["date"] and 
             saved["location"] == tournament["location"]), 
            None
        )
        if matching_saved:
            tournament["registration_closing_sent"] = matching_saved.get("registration_closing_sent", False)
            tournament["registration_filling_sent"] = matching_saved.get("registration_filling_sent", False)
        else:
            tournament["registration_closing_sent"] = False
            tournament["registration_filling_sent"] = False

    # Check for registration changes
    registration_opened = []
    closing_soon = []
    filling_up = []

    # Find tournaments where registration has newly opened
    for current in tournaments:
        matching_saved = next(
            (saved for saved in saved_tournaments if 
             saved["name"] == current["name"] and 
             saved["date"] == current["date"] and 
             saved["location"] == current["location"]), 
            None
        )
        
        if matching_saved and not matching_saved.get("registration_open", False) and current.get("registration_open", True):
            registration_opened.append(current)

    # Use the DetailWorker to fetch additional tournament details asynchronously
    # This doesn't block Discord's heartbeat because it uses a separate executor
    detail_worker = DetailWorker(thread_pool, max_concurrent=2)
    closing_soon, filling_up = await detail_worker.enrich_tournaments(tournaments)

    # Save the updated tournaments list back to S3
    await loop.run_in_executor(thread_pool, lambda: save_tournaments_to_s3(tournaments))

    return new_tournaments, registration_opened, closing_soon, filling_up

@client.event
async def on_ready():
    logging.info(f'{client.user} has connected to Discord!')
    try:
        if not check_tournaments.is_running():  # Ensure the task is not already running
            check_tournaments.start()  # Start the periodic task
    except Exception as e:
        logging.error(f"Failed to start background task: {e}")

# Use jitter in the task interval to avoid predictable patterns
# that could trigger rate limiting detection
def jittered_hours(hours=8):
    """Return the interval in minutes with +/- 25% random jitter"""
    base_minutes = hours * 60
    jitter = base_minutes * 0.25  # 25% jitter
    return base_minutes + random.uniform(-jitter, jitter)

@tasks.loop(hours=12)  # Run every 12 hours instead of every hour
async def check_tournaments():
    try:
        logging.info("Checking for new tournaments...")
        
        # Use async version of the tournament fetching to avoid blocking heartbeats
        tournaments = await fetch_tournaments_async()
        
        # Handle errors gracefully
        if not tournaments:
            logging.warning("No tournaments fetched. Skipping notification cycle.")
            return
            
        # Use async version of save_tournaments to avoid blocking
        new_tournaments, registration_opened, closing_soon, filling_up = await save_tournaments_async(tournaments)

        logging.info(f"Found {len(new_tournaments)} new tournaments")
        logging.info(f"Found {len(registration_opened)} tournaments with newly opened registration")
        logging.info(f"Found {len(closing_soon)} tournaments closing soon")
        logging.info(f"Found {len(filling_up)} tournaments filling up")

        channel = client.get_channel(CHANNEL_ID)
        if not channel:
            logging.error(f"Could not find Discord channel with ID {CHANNEL_ID}")
            return

        # Send messages for new tournaments
        for tournament in new_tournaments:
            logging.info(f"New tournament: {tournament['name']}")

            # Inside the loop where we create the embed
            embed = discord.Embed(
                title="🚨 New Local Tournament 🚨",
                description=f"[{tournament['name']}]({tournament['url']})\n\n"
                           f"**Location:** {tournament['location']}\n"
                           f"**Date:** {tournament['date']}\n"
                           f"**Registrants:** {tournament['registrants']}\n"
                           f"**Registration Open:** {'Yes' if tournament['registration_open'] else 'No'}",
                color=discord.Color.blue()
            )

            if tournament['tier']:
                embed.add_field(name="Tier", value=tournament['tier'], inline=False)

            await channel.send(embed=embed)
            await asyncio.sleep(0.5)  # Small delay between messages to avoid rate limits

        # Send messages for tournaments with newly opened registration
        for tournament in registration_opened:
            logging.info(f"Registration opened: {tournament['name']}")

            embed = discord.Embed(
                title="📖 Registration Open 📖",
                description=f"[{tournament['name']}]({tournament['url']})\n\n"
                        f"**Location:** {tournament['location']}\n"
                        f"**Date:** {tournament['date']}\n"
                        f"**Registrants:** {tournament['registrants']}\n"
                        f"**Registration Open:** {'Yes' if tournament['registration_open'] else 'No'}",
                color=discord.Color.green()
            )
            if tournament['tier']:
                embed.add_field(name="Tier", value=tournament['tier'], inline=False)
            
            await channel.send(embed=embed)
            await asyncio.sleep(0.5)  # Small delay between messages

        # Send messages for closing soon
        for tournament in closing_soon:
            registration_closing_message = tournament.get('closing_text', 'N/A')
            embed = discord.Embed(
                title="⏳ Registration Closing Soon ⏳",
                description=f"[{tournament['name']}]({tournament['url']})\n\n"
                        f"**Location:** {tournament['location']}\n"
                        f"**Tournament Date:** {tournament['date']}\n"
                        f"**Registration Closes:** {registration_closing_message}",
                color=discord.Color.orange()
            )
            await channel.send(embed=embed)
            await asyncio.sleep(0.5)  # Small delay between messages

        # Send messages for filling up
        for tournament in filling_up:
            embed = discord.Embed(
                title="🚨 Registration Filling Up 🚨",
                description=f"[{tournament['name']}]({tournament['url']})\n\n"
                        f"**Location:** {tournament['location']}\n"
                        f"**Date:** {tournament['date']}\n"
                        f"**Registrants:** {tournament['registrants']} / {tournament['capacity']}",
                color=discord.Color.red()
            )
            await channel.send(embed=embed)
            await asyncio.sleep(0.5)  # Small delay between messages
        
        # Add jitter to next run time to avoid predictable patterns
        # that might trigger rate limiting detection
        next_run = jittered_hours(12)  # 12 hours +/- 25% jitter
        check_tournaments.change_interval(minutes=int(next_run))
        logging.info(f"Next check scheduled in {next_run/60:.1f} hours")
        
    except Exception as e:
        logging.error(f"Error in check_tournaments task: {e}", exc_info=True)
        
        # If we encounter an error that might be due to rate limiting,
        # back off by increasing the next interval
        next_run = jittered_hours(24)  # 24 hours with jitter
        check_tournaments.change_interval(minutes=int(next_run))
        logging.info(f"Error occurred. Backing off - next check in {next_run/60:.1f} hours")

# Run the bot
client.run(TOKEN)
