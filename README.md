# 🚀 AI Engineer Case Study: Dutchie POS Analytics Dashboard

## Project Summary

This project delivers a responsive, one-screen analytics dashboard built using **Streamlit**, designed to provide retail managers with crucial Key Performance Indicators (KPIs) and actionable operational insights.

The solution utilizes a modern, high-performance data stack:
* **DuckDB** as the analytical database (OLAP).
* **Polars** for fast, memory-efficient data transformation and processing.
* **Streamlit** and **Plotly** for the interactive dashboard front-end.

The core objective is to deliver metrics like sales, AOV, discount rates, and exception (void/refund) rates, along with a visual heatmap to identify operational bottlenecks and coaching opportunities.

## 💾 Data Architecture & Pipeline

The application follows a standard ELT (Extract, Load, Transform) pattern:

1.  **Ingest (E & L):** Raw JSON/CSV files are loaded directly into the in-memory/on-disk **DuckDB** database (`data/dutchie.db`).
2.  **Transform (T):** The `clean_and_model()` function standardizes data, creates a star schema (Fact & Dimensions), calculates derived columns like `gross_sale`, and ensures data quality (e.g., handling nulls).
3.  **Metrics:** SQL queries (executed via DuckDB and Polars) calculate KPIs and aggregations required by the dashboard.

## ⚙️ Setup and Installation

Follow these steps to set up the project locally using a Python virtual environment.

### Prerequisites

* **Python 3.9+**
* **Git**

### Step 1: Clone the Repository

```bash
git clone [YOUR_GIT_REPOSITORY_LINK]
cd dutchie_pos_case