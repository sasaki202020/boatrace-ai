@echo off
py -3 -m src.pipeline.import_and_refresh_k_results --input-dir data/inbox/k_results --start-date 20260401 --end-date 20260425 --jcd all --stake 100
