"""Flask application factory for AiTrading Dashboard."""

import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from flask import Flask

from dashboard.db import init_db
from dashboard.routes.main import main_bp
from dashboard.routes.positions import positions_bp
from dashboard.routes.rankings import rankings_bp
from dashboard.routes.orders import orders_bp
from dashboard.routes.analysis import analysis_bp
from dashboard.routes.portfolio import portfolio_bp
from dashboard.api.data import api_bp

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_app(db_path=None):
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )

    # Resolve DB path from config.yaml if not provided
    if db_path is None:
        config_file = PROJECT_ROOT / "config.yaml"
        if config_file.exists():
            with open(config_file) as f:
                cfg = yaml.safe_load(f)
            db_path = cfg.get("database", {}).get("path", "data/trading.db")
        else:
            db_path = "data/trading.db"

    # Make relative paths relative to project root
    if not os.path.isabs(db_path):
        db_path = str(PROJECT_ROOT / db_path)

    app.config["DB_PATH"] = db_path
    app.config["SECRET_KEY"] = "aitrade-dashboard-local"

    # Register blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(positions_bp, url_prefix="/positions")
    app.register_blueprint(rankings_bp, url_prefix="/rankings")
    app.register_blueprint(orders_bp, url_prefix="/orders")
    app.register_blueprint(analysis_bp, url_prefix="/analysis")
    app.register_blueprint(portfolio_bp, url_prefix="/portfolio")
    app.register_blueprint(api_bp, url_prefix="/api/data")

    # Template filters
    @app.template_filter("currency")
    def currency_filter(value):
        if value is None:
            return "$0.00"
        return f"${value:,.2f}"

    @app.template_filter("pct")
    def pct_filter(value):
        if value is None:
            return "0.0%"
        return f"{value:+.1f}%"

    @app.template_filter("score_color")
    def score_color_filter(value):
        if value is None:
            return "text-gray-400"
        if value >= 75:
            return "text-emerald-400"
        if value >= 60:
            return "text-blue-400"
        if value >= 45:
            return "text-yellow-400"
        return "text-red-400"

    @app.template_filter("pnl_color")
    def pnl_color_filter(value):
        if value is None or value == 0:
            return "text-gray-400"
        return "text-emerald-400" if value > 0 else "text-red-400"

    @app.template_filter("timeago")
    def timeago_filter(dt_str):
        if not dt_str:
            return "—"
        try:
            dt = datetime.fromisoformat(str(dt_str))
        except (ValueError, TypeError):
            return str(dt_str)
        delta = datetime.now() - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"

    @app.template_filter("shortdate")
    def shortdate_filter(dt_str):
        if not dt_str:
            return "—"
        try:
            dt = datetime.fromisoformat(str(dt_str))
            return dt.strftime("%b %d, %H:%M")
        except (ValueError, TypeError):
            return str(dt_str)

    init_db(app)
    return app


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    app = create_app()
    print(f"Starting AiTrading Dashboard at http://127.0.0.1:5000")
    print(f"Database: {app.config['DB_PATH']}")
    app.run(host="127.0.0.1", port=5000, debug=True)
