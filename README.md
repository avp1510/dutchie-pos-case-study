# Dutchie POS Case Study

This repository contains a retail analytics dashboard built for Dutchie POS data. It ingests local files or live API responses, models the data inside DuckDB, and exposes operational KPIs through a Streamlit interface for store managers and analysts.

## What This Project Does

- Ingests JSON or CSV exports from Dutchie POS
- Supports live API fetches when an integrator key is configured
- Cleans and models data into analytics-friendly tables
- Computes KPIs such as net sales, AOV, items per ticket, void rate, refund rate, and discount impact
- Visualizes operational trends for store-level decision making

## Stack

- Python
- Streamlit
- DuckDB
- Polars
- Plotly
- ReportLab

## Project Structure

- `app.py`: Streamlit dashboard entry point
- `pipeline/ingest.py`: file ingestion logic
- `pipeline/transform.py`: cleaning and data modeling
- `pipeline/metrics.py`: KPI calculations
- `pipeline/fetch.py`: Dutchie API fetch helpers
- `data/`: local exports and DuckDB database

## Run Locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Skills Demonstrated

- Analytics dashboard development
- ELT pipeline design
- Data modeling with DuckDB
- Fast dataframe processing with Polars
- KPI design for retail operations
- Streamlit application development

## Notes

The app supports both offline local analysis and a live API-backed mode depending on environment configuration.
