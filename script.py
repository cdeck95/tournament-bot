import os
import discord
from discord.ext import tasks
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID'))  # Discord channel ID as integer

# Discord client
intents = discord.Intents.default()
client = discord.Client(intents=intents)

TOURNAMENTS_FILE = "tournaments.json"
TOURNAMENT_PAGE_URL = "https://www.discgolfscene.com/tournaments/options;distance=60;zip=08043;country=USA"

def fetch_tournaments():
    response = requests.get(TOURNAMENT_PAGE_URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    tournaments = []
    tournament_divs = soup.select(".tournament-U, .tournament-C")
    
    # Current date for year handling
    now = datetime.now()

    for div in tournament_divs:
        # Extract URL from the <a> tag
        link_tag = div.select_one("a")
        url = f"https://www.discgolfscene.com{link_tag['href']}" if link_tag and link_tag.has_attr('href') else "N/A"
        
        # Extract name and registration status
        title_em = div.select_one("em")
        name = title_em.text.strip() if title_em else "N/A"
        registration_open = "trego" in title_em.get("class", []) if title_em else False
        
        # Extract registrants and location
        registrants_span = div.find("span", string=lambda x: x and "Registrants:" in x)
        registrants = 0  # Default to 0 if registrants span is missing
        if registrants_span:
            try:
                registrants = int(registrants_span.text.split(":")[1].strip())
            except (ValueError, IndexError):
                registrants = 0  # Default to 0 if parsing fails

        # Find location span (next <span> after registrants or empty span)
        location_span = registrants_span.find_next("span") if registrants_span else div.find("span", string=lambda x: x and "at" in x)
        location = location_span.text.strip() if location_span else "N/A"
        
        # Parse and format date
        date_text = div.select_one(".t-date").text.strip() if div.select_one(".t-date") else None
        try:
            # First, parse the date without the year
            parsed_date = datetime.strptime(date_text, "%B %d %A")
            
            # Adjust the year dynamically
            if parsed_date.month < now.month:
                year = now.year + 1  # Tournament in the next year
            else:
                year = now.year
            
            # Reconstruct the date with the correct year
            full_date = parsed_date.replace(year=year)
            date = full_date.strftime("%m/%d/%Y")  # Format to MM/DD/YYYY

            # Skip past tournaments (just in case)
            if full_date < now:
                continue
        except ValueError:
            date = "N/A"
        
        tier = div.select_one(".info.ts").text.strip() if div.select_one(".info.ts") else None  # None if tier is missing

        tournaments.append({
            "name": name,
            "url": url,
            "registration_open": registration_open,
            "location": location,
            "date": date,
            "registrants": registrants,
            "tier": tier
        })

    return tournaments

def save_tournaments(tournaments):
    if not os.path.exists(TOURNAMENTS_FILE):
        with open(TOURNAMENTS_FILE, "w") as f:
            json.dump([], f)

    with open(TOURNAMENTS_FILE, "r") as f:
        saved_tournaments = json.load(f)

    # Identify new tournaments (unique by name, date, and location)
    new_tournaments = [
        t for t in tournaments if not any(
            t["name"] == saved["name"] and
            t["date"] == saved["date"] and
            t["location"] == saved["location"]
            for saved in saved_tournaments
        )
    ]

    # Check for registration changes
    registration_opened = []
    for current in tournaments:
        for saved in saved_tournaments:
            if (current["name"] == saved["name"] and
                current["date"] == saved["date"] and
                current["location"] == saved["location"] and
                not saved.get("registration_open", False) and
                current.get("registration_open", False)):
                registration_opened.append(current)

    # Save the updated tournaments list
    with open(TOURNAMENTS_FILE, "w") as f:
        json.dump(tournaments, f, indent=4)

    return new_tournaments, registration_opened

@client.event
async def on_ready():
    print(f'{client.user} has connected to Discord!')
    if not check_tournaments.is_running():  # Ensure the task is not already running
        check_tournaments.start()  # Start the periodic task


@tasks.loop(minutes=15)  # Run every 15 min
async def check_tournaments():
    print("Checking for new tournaments...")
    tournaments = fetch_tournaments()
    new_tournaments, registration_opened = save_tournaments(tournaments)

    if not new_tournaments:
        print("No new tournaments found.")
    if not registration_opened:
        print("No tournaments with newly opened registration found.")

    channel = client.get_channel(CHANNEL_ID)

    # Send messages for new tournaments
    for tournament in new_tournaments:
        print(f"New tournament: {tournament['name']}")
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
        # embed.add_field(name="Location", value=tournament['location'], inline=False)
        # embed.add_field(name="Date", value=tournament['date'], inline=True)
        # embed.add_field(name="Registrants", value=str(tournament['registrants']), inline=True)
        # embed.add_field(name="Registration Open", value="Yes" if tournament['registration_open'] else "No", inline=True)

        if tournament['tier']:
            embed.add_field(name="Tier", value=tournament['tier'], inline=False)

        await channel.send(embed=embed)

    # Send messages for tournaments with newly opened registration
    for tournament in registration_opened:
        print(f"Registration opened: {tournament['name']}")
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

# Run the bot
client.run(TOKEN)
