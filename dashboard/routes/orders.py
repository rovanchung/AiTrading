"""Orders page."""

from flask import Blueprint, render_template
from dashboard.db import query

orders_bp = Blueprint("orders", __name__)


@orders_bp.route("/")
def index():
    orders = query("SELECT * FROM orders ORDER BY submitted_at DESC")
    return render_template("orders.html", orders=orders)
