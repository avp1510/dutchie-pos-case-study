import streamlit as st
import os
import plotly.express as px
# Assume these are available in your pipeline folder
from pipeline.ingest import ingest_file
from pipeline.transform import clean_and_model
# Note: The original code imports these but they are called inside the button clicks
# from pipeline.metrics import get_kpis, get_exceptions_and_heatmap, get_same_store_sales
from pipeline.filters import get_filter_options
from pipeline.fetch import fetch_sales_json, save_json
from pipeline.config import INTEGRATOR_KEY 
from datetime import datetime, date, timedelta
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import ImageReader
from textwrap import wrap
import polars as pl
import numpy as np # <-- ADDED FOR NAN CHECKING

# --- Ensure data folder exists ---
os.makedirs("data", exist_ok=True)

# --- Streamlit App Configuration ---
st.set_page_config(page_title="Dutchie POS Dashboard", layout="wide")
mode = "LIVE (API)" if INTEGRATOR_KEY and not INTEGRATOR_KEY.startswith("mock") else "OFFLINE (Local Files)"
st.caption(f"**Mode:** {mode}")

# --- Header with Reset Button ---
col1, col2 = st.columns([6, 1])
with col1:
    st.title("Dutchie POS Manager Dashboard")
with col2:
    if st.button("🔴 Reset Data", key="reset_button"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        db_path = os.path.abspath("data/dutchie.db")
        if os.path.exists(db_path):
            os.remove(db_path)
        st.info("Dashboard reset. Re-upload or fetch data again.")

# --- 0. Fetch from Dutchie API (Live Only) ---
if INTEGRATOR_KEY and not INTEGRATOR_KEY.startswith("mock"):
    st.sidebar.header("🔄 Fetch from Dutchie POS API")
    stores = st.sidebar.multiselect("Select Stores to Fetch", ["Columbus", "Cincinnati"], default=["Columbus"])
    if st.sidebar.button("Fetch Data from API"):
        for store in stores:
            try:
                st.write(f"Fetching latest data for {store}...")
                data = fetch_sales_json(store)
                file_path = save_json(data, store)
                st.success(f"✅ Data fetched for {store} → {file_path}")
            except Exception as e:
                st.error(f"❌ Failed for {store}: {e}")
        st.info("Click 'Clean and Model Data' next.")

# --- 1. Data Ingestion ---
st.header("1. Data Ingestion")
uploads = st.file_uploader("Upload POS Export(s) (JSON or CSV)", type=["json", "csv"], accept_multiple_files=True)
if uploads:
    for uploaded in uploads:
        file_buffer = uploaded.getvalue()
        # Determine file type from Streamlit's detection or fall back to name
        file_type = uploaded.type.split('/')[-1] if uploaded.type else uploaded.name.split(".")[-1].lower() 
        
        table = uploaded.name.split(".")[0].lower()
        
        # Pass the buffer, table name, and file type to the ingestion function
        msg = ingest_file(file_buffer, table, file_type=file_type) 
        st.success(f"Uploaded & ingested: {uploaded.name}")


if st.button("Clean and Model Data", key="clean_button"):
    st.info(clean_and_model())

# --- Sidebar Filters ---
st.sidebar.header("Filters")
try:
    options = get_filter_options()
    location = st.sidebar.multiselect("Location", options["locations"])
    category = st.sidebar.multiselect("Category", options["categories"])
    staff = st.sidebar.multiselect("Cashier/Staff", options["staff"])
    daypart = st.sidebar.multiselect(
        "Daypart",
        ["Open–Noon", "Noon–5", "5–Close"],
    )
    
    # Order Type Filter (based on TENDER TYPE)
    order_type = st.sidebar.multiselect(
        "Order Type (Tender Type)",
        ["CASH", "CARD", "ONLINE", "DEBIT", "CREDIT", "OTHER"], # Added "OTHER" as a common fallback
    )
    
    # Date Range Filter
    today = date.today()
    
    # Determine default date range: all available dates or just today
    date_options = options.get("dates", [])
    if date_options and len(date_options) >= 2:
        # Convert date strings from get_filter_options to date objects
        start_date = datetime.strptime(date_options[0], '%Y-%m-%d').date()
        end_date = datetime.strptime(date_options[-1], '%Y-%m-%d').date()
        date_range_default = [start_date, end_date]
    else:
        # Fallback to a 7-day range ending today if no data is loaded
        default_end = today
        default_start = today - timedelta(days=6) 
        date_range_default = [default_start, default_end]

    date_range = st.sidebar.date_input("Date Range", date_range_default, key="date_range_filter")

    # Ensure date_range is always a list/tuple of two dates
    if isinstance(date_range, (list, tuple)) and len(date_range) == 1:
        date_range = [date_range[0], date_range[0]]
    elif not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
        # Fallback if the user cleared the date input or it's malformed
        date_range = [today, today]

    filters = {
        "locations": location or ["ALL"],
        "categories": category or ["ALL"],
        "staff": staff or ["ALL"],
        "daypart": daypart or ["ALL"],
        "order_types": order_type or ["ALL"], # Corresponds to fs.tender_type
        "date_range": date_range,
    }

except Exception:
    st.sidebar.info("Load & clean data first.")
    # Default filters when data isn't ready
    filters = {
        "locations": ["ALL"], 
        "categories": ["ALL"], 
        "staff": ["ALL"], 
        "daypart": ["ALL"],
        "order_types": ["ALL"],
        "date_range": [date.today(), date.today()],
    }

# --- 2. KPIs ---
st.header("2. Key Performance Indicators")
# Initialize metrics key if not present
if "metrics" not in st.session_state:
    st.session_state.metrics = None
if st.button("Compute KPIs", key="kpi_button"):
    from pipeline.metrics import get_kpis
    st.session_state.metrics = get_kpis(filters)

metrics = st.session_state.metrics
if metrics:
    
    # 1. CORE & VOID/REFUND METRICS
    st.subheader("Core Performance & Exception Rates")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Net Sales ($)", f"$ {round(metrics['total_sales'], 2):,}")
    col2.metric("Avg Order Value ($)", f"$ {round(metrics['avg_order_value'], 2):,}")
    col3.metric("Items per Ticket", round(metrics["items_per_ticket"], 2))
    col4.metric("Void Rate (%)", f"{round(metrics['void_rate'], 2)} %")
    col5.metric("Refund Rate (%)", f"{round(metrics['refund_rate'], 2)} %")
    
    st.divider()

    # 2. DISCOUNT/PROMO IMPACT
    st.subheader("Discount & Promotional Impact")
    colA, colB, colC, colD = st.columns([1.5, 1.5, 2, 3])
    
    with colA:
        colA.metric(
            "Discount Rate (%)", 
            f"{round(metrics['discount_rate'], 2)} %",
            help="Total Discount Amount as a percentage of Gross Sales (before discounts)."
        )
    with colB:
        # AOV with/without promo
        colB.metric(
            "AOV (With Promo)", 
            f"$ {round(metrics['aov_with_promo'], 2):,}"
        )
    with colC:
        colC.metric(
            "AOV (Full Price)", 
            f"$ {round(metrics['aov_without_promo'], 2):,}"
        )
    
    with colD:
        st.caption("Top 5 Active Promos (by Discount $)")
        # Safely convert Polars DataFrame to Pandas
        df_promo = metrics["top_promos"].to_pandas() if hasattr(metrics["top_promos"], "to_pandas") else metrics["top_promos"]
        if not df_promo.empty:
            st.dataframe(
                df_promo.rename(columns={'promo_applied': 'Promo Name', 'total_discount_amount': 'Discount $', 'total_uses': 'Uses'})
                        .set_index('Promo Name')
                        .style.format({'Discount $': '${:,.2f}'}),
                use_container_width=True,
                hide_index=False
            )
        else:
            st.info("No active promos found in this period.")

    st.divider()

    # 3. CATEGORY AND TENDER MIX
    col_cat, col_tender = st.columns(2)

    with col_cat:
        st.subheader("Sales by Category")
        df_cat = metrics["category_sales"].to_pandas() if hasattr(metrics["category_sales"], "to_pandas") else metrics["category_sales"]
        fig_cat = px.bar(
            df_cat, x="category", y="category_sales", text_auto=".2s",
            title="Category Sales Distribution", color="category_sales",
            labels={"category_sales": "Total Sales ($)", "category": "Category"}
        )
        st.plotly_chart(fig_cat, use_container_width=True)
        st.session_state["fig_category"] = fig_cat

    with col_tender:
        st.subheader("Basket Economics: Tender Mix")
        df_tender = metrics["tender_mix"].to_pandas() if hasattr(metrics["tender_mix"], "to_pandas") else metrics["tender_mix"]
        
        if not df_tender.empty:
            fig_tender = px.pie(
                df_tender, 
                values='tender_sales', 
                names='tender_type', 
                title='Net Sales Distribution by Tender Type',
                hole=.3,
                labels={"tender_sales": "Sales ($)", "tender_type": "Tender Type"}
            )
            fig_tender.update_traces(textinfo='percent+label', marker=dict(line=dict(color='#000000', width=1)))
            st.plotly_chart(fig_tender, use_container_width=True)
            st.session_state["fig_tender"] = fig_tender
        else:
            st.info("No tender mix data available for selected filters.")

    # --- NEW SECTION 2.5: Product Performance (Top/Bottom Movers) ---
    st.divider()
    st.subheader("2.5 Product Performance: Top/Bottom Movers 🚀")
    
    df_movers = metrics.get("product_movers")
    
    if df_movers is not None and not df_movers.is_empty():
        df_movers_pd = df_movers.to_pandas()
        
        col_net_sales, col_quantity = st.columns(2)
        
        # --- Top/Bottom by Net Sales ---
        with col_net_sales:
            st.caption("Top/Bottom 10 by Net Sales ($)")
            
            # Sort by net_sales and select top 5 and bottom 5
            df_sorted_sales = df_movers_pd.sort_values(by="net_sales", ascending=False)
            
            top_sales = df_sorted_sales.head(5).rename(columns={'product_name': 'Product', 'net_sales': 'Net Sales $', 'category': 'Category'}).set_index('Product')
            
            # Use tail(5) to get the 5 lowest performing products
            bottom_sales = df_sorted_sales.tail(5).rename(columns={'product_name': 'Product', 'net_sales': 'Net Sales $', 'category': 'Category'}).set_index('Product')
            
            st.markdown("**Top 5 SKUs (Net Sales)**")
            st.dataframe(
                top_sales[['Net Sales $', 'Category']].style.format({'Net Sales $': '${:,.2f}'}),
                use_container_width=True
            )
            
            st.markdown("**Bottom 5 SKUs (Net Sales)**")
            st.dataframe(
                bottom_sales[['Net Sales $', 'Category']].style.format({'Net Sales $': '${:,.2f}'}),
                use_container_width=True
            )
            
        # --- Top/Bottom by Quantity Sold ---
        with col_quantity:
            st.caption("Top/Bottom 10 by Quantity Sold (Units)")
            
            # Sort by quantity_sold and select top 5 and bottom 5
            df_sorted_quantity = df_movers_pd.sort_values(by="quantity_sold", ascending=False)
            
            top_quantity = df_sorted_quantity.head(5).rename(columns={'product_name': 'Product', 'quantity_sold': 'Qty Sold', 'category': 'Category'}).set_index('Product')
            bottom_quantity = df_sorted_quantity.tail(5).rename(columns={'product_name': 'Product', 'quantity_sold': 'Qty Sold', 'category': 'Category'}).set_index('Product')
            
            st.markdown("**Top 5 SKUs (Quantity Sold)**")
            st.dataframe(
                top_quantity[['Qty Sold', 'Category']].style.format({'Qty Sold': '{:,.0f}'}),
                use_container_width=True
            )
            
            st.markdown("**Bottom 5 SKUs (Quantity Sold)**")
            st.dataframe(
                bottom_quantity[['Qty Sold', 'Category']].style.format({'Qty Sold': '{:,.0f}'}),
                use_container_width=True
            )

    else:
        st.info("No product performance data found for selected filters.")

    st.divider() 
# --- END NEW SECTION 2.5 ---

# --- 3. Operational View ---
st.header("3. Operational View")
# Initialize heatmap key if not present
if "heatmap_data" not in st.session_state:
    st.session_state.heatmap_data = None

if st.button("Exception & Heatmap View", key="heatmap_button"):
    from pipeline.metrics import get_exceptions_and_heatmap
    st.session_state.heatmap_data = get_exceptions_and_heatmap(filters)

# ✅ Use .get() to safely read heatmap data
heatmap_result = st.session_state.get("heatmap_data")
if heatmap_result:
    df_heatmap, spikes = heatmap_result # Unpack the tuple
    
    if df_heatmap is not None and not df_heatmap.is_empty():
        # --- UPDATED SUBHEADER AND PLOTLY CALL FOR COACHING WINDOWS ---
        st.subheader("Hourly Throughput and Exceptions")
        df_heatmap_pd = df_heatmap.to_pandas() if hasattr(df_heatmap, "to_pandas") else df_heatmap # Use df_heatmap_pd locally
        
        # Use total_txns for Z (throughput) and add discount/exception data to hover
        fig = px.density_heatmap(
            df_heatmap_pd, # Use the pandas DataFrame
            x="hour",
            y="location", 
            z="total_txns", 
            # Include sales, discount data, and rates in the hover data
            hover_data=["sales", "total_discount", "discount_rate", "voids", "refunds"], 
            color_continuous_scale="Viridis",
            title="Hourly Transaction Throughput (Hover for Exceptions/Discounts)",
            labels={
                "hour": "Hour", 
                "location": "Store", 
                "z": "Total Transactions"
            }
        )
        
        # 🟢 NEW: Add custom hover template to format data clearly
        fig.update_traces(
            hovertemplate=(
                "<b>Store:</b> %{y}<br>"
                "<b>Hour:</b> %{x}<br>"
                "<b>Total Transactions:</b> %{z}<br>"
                "<hr>"
                "Sales: $%{customdata[0]:,.2f}<br>"
                "Total Discount: $%{customdata[1]:,.2f}<br>"
                "Discount Rate: %{customdata[2]:,.2f}%%<br>"
                "Voids: %{customdata[3]:.0f}<br>"
                "Refunds: %{customdata[4]:.0f}<br>"
            ),
            # customdata maps to hover_data:
            # 0: sales, 1: total_discount, 2: discount_rate, 3: voids, 4: refunds
            customdata=df_heatmap_pd[["sales", "total_discount", "discount_rate", "voids", "refunds"]].values
        )

        st.plotly_chart(fig, use_container_width=True)
        st.session_state["fig_heatmap"] = fig

        # --- Manager-friendly Exception Report ---
        if spikes:
            st.subheader("🚨 Exception Report")
            void_spikes = spikes["void_spikes"].to_pandas() if hasattr(spikes["void_spikes"], "to_pandas") else spikes["void_spikes"]
            refund_spikes = spikes["refund_spikes"].to_pandas() if hasattr(spikes["refund_spikes"], "to_pandas") else spikes["refund_spikes"]

            # Helper function to check for NaN in staff_id
            def is_nan(value):
                return (isinstance(value, float) and np.isnan(value)) or value is None
                
            if not void_spikes.empty:
                st.warning("⚠️ Voids Detected (Spikes)")
                grouped = void_spikes.groupby(["location", "daypart", "staff_id"], dropna=False).size().reset_index(name="count")
                for _, row in grouped.iterrows():
                    # Check if staff_id is NaN 
                    staff_display = row['staff_id'] if not is_nan(row['staff_id']) else "N/A"
                    st.write(f"• **{row['location']}** — {row['count']} void(s) during {row['daypart']} (Staff: **{staff_display}**)")

            if not refund_spikes.empty:
                st.warning("⚠️ Refunds Detected (Spikes)")
                grouped = refund_spikes.groupby(["location", "daypart", "staff_id"], dropna=False).size().reset_index(name="count")
                for _, row in grouped.iterrows():
                    # Check if staff_id is NaN 
                    staff_display = row['staff_id'] if not is_nan(row['staff_id']) else "N/A"
                    st.write(f"• **{row['location']}** — {row['count']} refund(s) during {row['daypart']} (Staff: **{staff_display}**)")
    else:
        st.warning("No data for heatmap.")


# --- 4. Sales Trends (WoW Same-Store Sales) ---
st.header("4. Sales Trends (Week-over-Week)")

# Get available locations for the toggle, or default to all filter locations
try:
    available_locations = get_filter_options()["locations"]
except Exception:
    available_locations = ["Columbus", "Cincinnati"] # Fallback

if not available_locations:
    available_locations = ["Columbus", "Cincinnati"] # Fallback if DB is empty

# Location toggle for WoW comparison
selected_wow_location = st.selectbox(
    "Select Location for Same-Store WoW Analysis",
    available_locations,
    key="wow_location_select"
)

# Initialize WoW metrics key
if "wow_metrics" not in st.session_state:
    st.session_state.wow_metrics = None

# Create a modified filter for the WoW calculation (only one location)
wow_filters = filters.copy()
wow_filters["locations"] = [selected_wow_location]

if st.button(f"Compute WoW Sales for {selected_wow_location}", key="wow_button"):
    from pipeline.metrics import get_same_store_sales
    # Ensure date range is valid for calculation
    if len(wow_filters["date_range"]) == 2 and wow_filters["date_range"][1] > wow_filters["date_range"][0]:
        st.session_state.wow_metrics = get_same_store_sales(wow_filters)
    else:
        st.error("Please select a valid date range (start date before end date) for WoW calculation.")


wow_metrics = st.session_state.get("wow_metrics")

if wow_metrics is not None and not wow_metrics.is_empty():
    
    # Filter the Polars DataFrame for the selected location (optional, since the query already filtered)
    location_row_df = wow_metrics.filter(pl.col("location") == selected_wow_location).to_pandas()
    
    if not location_row_df.empty:
        location_row = location_row_df.iloc[0]
        sales = location_row['net_sales']
        last_week_sales = location_row['last_week_sales']
        wow_change = location_row['wow_change']

        # Determine the range for display
        start_date = wow_filters["date_range"][0]
        end_date = wow_filters["date_range"][1]
        
        last_week_start = start_date - timedelta(days=7)
        last_week_end = end_date - timedelta(days=7)

        colA, colB, colC = st.columns(3)
        
        colA.metric(
            f"Net Sales ({start_date} to {end_date})", 
            f"$ {sales:,.2f}"
        )
        
        colB.metric(
            f"Last Week Net Sales ({last_week_start} to {last_week_end})", 
            f"$ {last_week_sales:,.2f}"
        )
        
        # --- FIXED LINE: Use numpy.isnan() to check for NaN (missing) values ---
        is_nan_or_none = (wow_change is None) or (isinstance(wow_change, float) and np.isnan(wow_change))

        # Format the delta for display
        delta_val = f"{wow_change:,.2f} %" if not is_nan_or_none else "N/A"
        
        # The delta parameter needs a raw number for coloring
        delta_raw = wow_change if not is_nan_or_none else None

        colC.metric(
            "WoW % Change", 
            delta_val, 
            delta=f"{delta_raw:,.2f} %" if delta_raw is not None else None,
            delta_color="normal"
        )
    else:
        st.info(f"No sales data found for {selected_wow_location} in the selected date range and filters.")
else:
    st.info("Click the 'Compute WoW Sales' button above to see the comparison.")


# --- 5. Manager Notes ---
st.header("5. Manager Notes") 
# Manager notes are stored in session state
notes = st.text_area(
    "Notes for Today", 
    placeholder="Add notes about key findings, coaching opportunities, or actions taken...", 
    height=150, 
    key="manager_notes_text",
    value=st.session_state.get("manager_notes_text", "")
)

def fig_to_png_bytes(fig, width=1000, height=600, scale=2):
    """Converts a Plotly figure to PNG bytes for ReportLab."""
    buf = BytesIO()
    fig.write_image(buf, format="png", width=width, height=height, scale=scale)
    buf.seek(0)
    return buf

def build_pdf(metrics, fig_category, fig_heatmap, notes, filters):
    """Generates the PDF report using ReportLab."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    W, H = LETTER
    margin = 36
    y = H - margin

    # --- Title and Date ---
    title = "Dutchie POS Manager Dashboard"
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.setFont("Helvetica-Bold", 14); c.drawString(margin, y, title)
    c.setFont("Helvetica", 10); c.drawRightString(W - margin, y, f"Generated: {date_str}")
    y -= 18
    
    # --- Filters Summary ---
    date_range = filters.get('date_range', [None, None])
    start_date_str = date_range[0].strftime('%Y-%m-%d') if date_range[0] else 'N/A'
    end_date_str = date_range[1].strftime('%Y-%m-%d') if date_range[1] else 'N/A'

    filt_txt_parts = [
        f"Locations: {', '.join(filters.get('locations', ['ALL']))}",
        f"Categories: {', '.join(filters.get('categories', ['ALL']))}",
        f"Staff: {', '.join(filters.get('staff', ['ALL']))}",
        f"Daypart: {', '.join(filters.get('daypart', ['ALL']))}",
        f"Order Type: {', '.join(filters.get('order_types', ['ALL']))}",
        f"Date Range: {start_date_str} to {end_date_str}"
    ]
    filt_txt = " | ".join(filt_txt_parts)
    
    c.setFont("Helvetica-Oblique", 9); c.drawString(margin, y, filt_txt[:120])
    y -= 24 # More space after filters

    # --- KPI Section ---
    c.setFont("Helvetica-Bold", 12); c.drawString(margin, y, "Key Performance Indicators")
    y -= 16; c.setFont("Helvetica", 11)
    kpi_lines = [
        f"Total Sales: ${metrics['total_sales']:.2f}",
        f"Avg Order Value: ${metrics['avg_order_value']:.2f}",
        f"Items per Ticket: {metrics['items_per_ticket']:.2f}",
        f"Discount Rate: {metrics['discount_rate']:.2f}%",
        f"AOV (w/ Promo): ${metrics['aov_with_promo']:.2f}",
        f"AOV (Full Price): ${metrics['aov_without_promo']:.2f}",
        f"Void Rate: {metrics['void_rate']:.2f}%",
        f"Refund Rate: {metrics['refund_rate']:.2f}%",
    ]
    # Draw KPIs in two columns for better use of space
    col_width = W / 2 - margin * 1.5
    for i, ln in enumerate(kpi_lines):
        if i < 4:
            c.drawString(margin, y, ln)
        else:
            c.drawString(margin + col_width, y, ln)
        
        if i % 2 == 0: # Move up for the next line
            y -= 14
    y += 14 * (len(kpi_lines) // 2) # Adjust y back to where the last line ended
    
    y -= 14

    # --- WoW Sales Summary ---
    if st.session_state.get("wow_metrics") is not None and not st.session_state["wow_metrics"].is_empty():
        y -= 8
        c.setFont("Helvetica-Bold", 12); c.drawString(margin, y, "WoW Sales Comparison")
        y -= 12
        wow_df = st.session_state["wow_metrics"].to_pandas().iloc[0]
        wow_change = wow_df['wow_change']
        
        is_nan_wow = (wow_change is None) or (isinstance(wow_change, float) and np.isnan(wow_change))
        wow_change_str = f"{wow_change:,.2f} %" if not is_nan_wow else "N/A"
        
        c.setFont("Helvetica", 10)
        c.drawString(margin, y, f"Location: {wow_df['location']}")
        c.drawString(margin + col_width, y, f"Net Sales: ${wow_df['net_sales']:.2f}")
        y -= 12
        c.drawString(margin, y, f"Last Week Sales: ${wow_df['last_week_sales']:.2f}")
        c.drawString(margin + col_width, y, f"WoW Change: {wow_change_str}")
        y -= 12
    y -= 8
    
    # --- Tender Mix Summary ---
    c.setFont("Helvetica-Bold", 12); c.drawString(margin, y, "Tender Mix (Top 5)")
    y -= 12
    tender_df = metrics.get('tender_mix')
    if tender_df is not None and not tender_df.is_empty():
        top_tenders = tender_df.sort("tender_sales", descending=True).head(5).to_pandas()
        for _, row in top_tenders.iterrows():
            # Calculate percentage for display if not already available
            total_sales = metrics['total_sales']
            percentage = (row['tender_sales'] / total_sales) * 100 if total_sales else 0
            line = f"{row['tender_type']}: ${row['tender_sales']:.2f} ({percentage:.2f}%)"
            c.setFont("Helvetica", 10)
            c.drawString(margin, y, line); y -= 12
        y -= 8 

    # --- Product Movers Summary ---
    c.setFont("Helvetica-Bold", 12); c.drawString(margin, y, "Product Performance Summary")
    y -= 12
    movers_df = metrics.get('product_movers')
    if movers_df is not None and not movers_df.is_empty():
        top_sales_product = movers_df.sort("net_sales", descending=True).head(1).to_pandas().iloc[0]
        top_qty_product = movers_df.sort("quantity_sold", descending=True).head(1).to_pandas().iloc[0]
        
        c.setFont("Helvetica", 10)
        c.drawString(margin, y, f"Top Selling by $: {top_sales_product['product_name']} (${top_sales_product['net_sales']:.2f})")
        y -= 12
        c.drawString(margin, y, f"Top Selling by Qty: {top_qty_product['product_name']} ({top_qty_product['quantity_sold']:.0f} units)")
        y -= 12
    else:
        c.setFont("Helvetica", 10); c.drawString(margin, y, "No product performance data available.")
        y -= 12
    y -= 8
        
    # --- Category Chart ---
    if fig_category:
        if y < 380: c.showPage(); y = H - margin
        c.setFont("Helvetica-Bold", 12); c.drawString(margin, y, "Category Sales Distribution")
        y -= 12
        img = ImageReader(fig_to_png_bytes(fig_category))
        c.drawImage(img, margin, y - 250, width=540, height=250, preserveAspectRatio=True) # Smaller size for better fit
        y -= 260
        
    # --- Heatmap Chart ---
    if fig_heatmap:
        if y < 380: c.showPage(); y = H - margin
        c.setFont("Helvetica-Bold", 12); c.drawString(margin, y, "Sales Throughput Heatmap")
        y -= 12
        img = ImageReader(fig_to_png_bytes(fig_heatmap))
        c.drawImage(img, margin, y - 250, width=540, height=250, preserveAspectRatio=True) # Smaller size for better fit
        y -= 260

    # --- Manager Notes ---
    if notes:
        if y < 120: c.showPage(); y = H - margin
        c.setFont("Helvetica-Bold", 12); c.drawString(margin, y, "Manager Notes")
        y -= 14; c.setFont("Helvetica", 10)
        # Use a smaller wrap width for PDF readability
        for line in wrap(notes, width=90): 
            c.drawString(margin, y, line)
            y -= 12
            if y < margin:
                c.showPage()
                y = H - margin

    c.showPage(); c.save()
    buf.seek(0)
    return buf

st.divider()
if st.button("📄 Export Dashboard to PDF", type="primary"):
    if not st.session_state.get("metrics"):
        st.error("Please compute the Key Performance Indicators first before exporting.")
    else:
        # Check if figures are available for cleaner PDF generation
        fig_cat = st.session_state.get("fig_category")
        fig_heat = st.session_state.get("fig_heatmap")
        
        # Give a warning if a key section is missing (e.g., if heatmap button wasn't pressed)
        if not fig_heat:
             st.warning("Note: Heatmap not computed. It will be excluded from the PDF.")

        pdf_buf = build_pdf(
            metrics=st.session_state["metrics"],
            fig_category=fig_cat,
            fig_heatmap=fig_heat,
            notes=st.session_state.get("manager_notes_text", ""),
            filters=filters,
        )
        fname = f"dutchie_dashboard_report_{datetime.now().strftime('%Y-%m-%d')}.pdf"
        st.download_button(
            label="✅ Download PDF Report",
            data=pdf_buf,
            file_name=fname,
            mime="application/pdf",
        )
