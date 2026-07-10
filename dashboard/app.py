import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dash
from dash import dcc, html, Input, Output, dash_table
import dash_bootstrap_components as dbc
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
from sqlalchemy import create_engine
import shap
from src.model import explain_fund_drift
from dotenv import load_dotenv

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.FLATLY])

def get_leaderboard(as_of_date):
    return pd.read_sql(
        f"SELECT * FROM drift_scores WHERE as_of_date='{as_of_date}' ORDER BY style_purity_score ASC",
        engine
    )

app.layout = dbc.Container([
    html.H2("Mutual Fund Style Purity Tracker", className="mt-4 mb-1"),
    html.P("Mandate compliance monitoring for Indian mutual funds — built on public AMFI data", 
           className="text-muted mb-4"),
    
    dbc.Row([
        dbc.Col([
            dcc.Dropdown(id="date-picker",
                options=[{"label": d, "value": d} for d in 
                         pd.read_sql("SELECT DISTINCT as_of_date FROM drift_scores ORDER BY 1 DESC", engine)["as_of_date"]],
                value=None, placeholder="Select month"),
        ], width=4),
    ]),
    
    html.Hr(),
    html.H5("Mandate Violation Leaderboard"),
    html.P("Funds ranked by style purity score (lowest = most drifted from mandate)", className="text-muted"),
    
    dash_table.DataTable(
        id="leaderboard-table",
        columns=[
            {"name": "Fund", "id": "fund_name"},
            {"name": "Category", "id": "sebi_category"},
            {"name": "Style Purity", "id": "style_purity_score"},
            {"name": "Cap Tier Score", "id": "cap_tier_score"},
            {"name": "HHI", "id": "hhi_concentration"},
            {"name": "Churn", "id": "churn_rate"},
            {"name": "Drifting?", "id": "drift_flag"},
        ],
        style_data_conditional=[
            {"if": {"filter_query": "{drift_flag} = true"}, "backgroundColor": "#fee2e2", "color": "#991b1b"},
        ],
        sort_action="native",
        style_table={"overflowX": "auto"},
    ),
    
    html.Hr(),
    html.H5("SHAP Attribution — which stocks are driving the drift?"),
    dbc.Row([
        dbc.Col([
            dcc.Dropdown(id="fund-picker", placeholder="Select a fund to inspect"),
        ], width=6),
    ]),
    dcc.Graph(id="shap-waterfall"),
    
    html.Hr(),
    html.H5("Score trends over time"),
    dcc.Graph(id="trend-chart"),
], fluid=True)

@app.callback(Output("leaderboard-table", "data"), Input("date-picker", "value"))
def update_leaderboard(date):
    if not date:
        return []
    df = get_leaderboard(date)
    df["drift_flag"] = df["drift_flag"].map({True: "⚠ Yes", False: "✓ Clean"})
    return df.to_dict("records")

@app.callback(Output("fund-picker", "options"), Input("date-picker", "value"))
def update_fund_picker(date):
    if not date:
        return []
    df = get_leaderboard(date)
    return [{"label": row["fund_name"], "value": row["scheme_code"]} for _, row in df.iterrows()]

@app.callback(Output("shap-waterfall", "figure"), 
              [Input("fund-picker", "value"), Input("date-picker", "value")])
def update_shap(scheme_code, date):
    if not scheme_code or not date:
        return go.Figure()
    df_shap = explain_fund_drift(scheme_code, date)
    fig = go.Figure(go.Bar(
        x=df_shap["shap_contribution"],
        y=df_shap["stock_name"],
        orientation="h",
        marker_color=df_shap["shap_contribution"].apply(lambda x: "#ef4444" if x > 0 else "#22c55e")
    ))
    fig.update_layout(title="Top stocks contributing to mandate drift (SHAP values)",
                      xaxis_title="SHAP contribution", yaxis_title="Stock",
                      height=400)
    return fig

@app.callback(Output("trend-chart", "figure"), Input("fund-picker", "value"))
def update_trend(scheme_code):
    if not scheme_code:
        return go.Figure()
    df = pd.read_sql(
        f"SELECT as_of_date, style_purity_score, cap_tier_score, churn_rate FROM drift_scores WHERE scheme_code='{scheme_code}' ORDER BY as_of_date",
        engine
    )
    fig = px.line(df, x="as_of_date", y=["style_purity_score","cap_tier_score","churn_rate"],
                  title="Score trends over time", labels={"value":"Score","variable":"Metric"})
    fig.add_hline(y=0.70, line_dash="dash", line_color="red", annotation_text="Drift threshold")
    return fig

if __name__ == "__main__":
    app.run(debug=True)