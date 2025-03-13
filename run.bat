@echo off
echo Starting Tournament Bot...

:: Check if virtual environment exists
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

:: Activate virtual environment
call venv\Scripts\activate

:: Install requirements
echo Installing/updating requirements...
pip install -r requirements.txt

:: Run the bot
echo Starting tournament bot...
python script.py

:: If script exits, pause so user can see any errors
pause
