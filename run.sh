#!/usr/bin/env sh
# One command: create venv, install, seed (automatic on first start) and run on http://localhost:5000
python3 -m venv .venv && . .venv/bin/activate && pip install -q -r requirements.txt && python app.py
