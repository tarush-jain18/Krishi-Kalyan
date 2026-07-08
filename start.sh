#!/bin/bash

python scripts/download_models.py

uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}