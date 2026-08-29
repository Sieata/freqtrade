@echo off
cd /d C:\Users\fs2023\Documents\freqtrade
".venv\Scripts\python.exe" user_data\scripts\oi_accumulate.py >> user_data\logs\oi_accumulate_cron.log 2>&1
