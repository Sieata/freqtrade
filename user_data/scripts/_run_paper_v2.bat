@echo off
cd /d "C:\Users\fs2023\Documents\freqtrade"
"C:\Users\fs2023\Documents\freqtrade\.venv\Scripts\python.exe" -m freqtrade trade --config user_data/config_paper_v2.json --strategy WeekendReverseV2 --logfile user_data/logs/paper_v2_forwardtest.log >> user_data/logs/paper_v2_launcher.log 2>&1
