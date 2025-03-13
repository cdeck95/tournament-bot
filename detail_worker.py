"""
This module provides async worker functionality to fetch tournament details
without blocking the Discord event loop.
"""
import asyncio
import logging
import time
import random
from datetime import datetime
from fetch_registration_details import fetch_registration_details

class DetailWorker:
    """Worker class to fetch tournament details asynchronously"""
    
    def __init__(self, executor, max_concurrent=2):
        """
        Initialize the worker
        
        Args:
            executor: ThreadPoolExecutor to use for background tasks
            max_concurrent: Maximum number of concurrent detail requests
        """
        self.executor = executor
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    async def get_tournament_details(self, tournament):
        """
        Fetch additional details for a tournament asynchronously
        
        Args:
            tournament: Tournament dictionary with at least a URL field
        
        Returns:
            Dictionary with additional tournament details
        """
        async with self.semaphore:
            loop = asyncio.get_running_loop()
            details = await loop.run_in_executor(
                self.executor, 
                lambda: fetch_registration_details(tournament["url"])
            )
            return details
    
    async def enrich_tournaments(self, tournaments):
        """
        Process a list of tournaments and enrich with details
        
        Args:
            tournaments: List of tournament dictionaries
        
        Returns:
            Lists of tournaments needing special notifications
        """
        closing_soon = []
        filling_up = []
        
        tasks = []
        
        # Filter tournaments that need additional details
        for tournament in tournaments:
            should_check_closing = False
            should_check_filling = False
            
            # Check if it needs details fetched
            try:
                if tournament["date"] != "N/A":
                    date_obj = datetime.strptime(tournament["date"], "%m/%d/%Y")
                    days_until_tournament = (date_obj - datetime.now()).days
                    should_check_closing = days_until_tournament <= 14 and not tournament.get("registration_closing_sent", False)
            except (ValueError, TypeError) as e:
                logging.warning(f"Date parsing error for {tournament['name']}: {e}")
            
            should_check_filling = tournament["registrants"] >= 30 and not tournament.get("registration_filling_sent", False)
            
            # Only fetch details when needed and registration is open
            if tournament["url"] != "N/A" and tournament.get("registration_open", False) and (should_check_closing or should_check_filling):
                tasks.append((tournament, self.get_tournament_details(tournament)))
        
        # Process results as they complete
        for tournament, task in tasks:
            try:
                details = await task
                tournament.update(details)  # Add fetched details to the tournament dictionary
                
                # Check for "closing soon"
                if details["closing_date"] and tournament.get("date") != "N/A":
                    try:
                        days_left = (details["closing_date"] - datetime.now()).days
                        if days_left < 7:
                            closing_soon.append(tournament)
                            tournament["registration_closing_sent"] = True
                    except Exception as e:
                        logging.warning(f"Error calculating days left: {e}")
                
                # Check for "filling up"
                if details["capacity"] > 0:  # Avoid division by zero
                    fill_percentage = (details["registrants"] / details["capacity"]) * 100
                    if fill_percentage >= 75:
                        filling_up.append(tournament)
                        tournament["registration_filling_sent"] = True
            
            except Exception as e:
                logging.error(f"Error fetching details for {tournament['name']}: {e}")
        
        return closing_soon, filling_up
