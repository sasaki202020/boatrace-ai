@echo off
"C:\Users\goo10\AppData\Local\Programs\Python\Python312\python.exe" -m src.pipeline.import_and_refresh_k_results --input-dir data/inbox/k_results --start-date 20260401 --end-date 20260425 --jcd all --stake 100
