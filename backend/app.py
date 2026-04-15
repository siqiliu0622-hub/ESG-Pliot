from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_file

try:
    from flask_cors import CORS
except ImportError:  # pragma: no cover - optional dependency
    CORS = None


BASE_DIR = Path(__file__).resolve().parent.parent
DB_DIR = BASE_DIR / "backend" / "data"
DB_PATH = DB_DIR / "esg.db"
FRONTEND_FILE = BASE_DIR / "ai_studio_code (5).html"


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    if CORS is not None:
        CORS(app)

    init_db()

    @app.get("/")
    def index() -> Any:
        if FRONTEND_FILE.exists():
            return send_file(FRONTEND_FILE)
        return jsonify({"message": "frontend file not found"}), 404

    @app.get("/api/health")
    def health() -> Any:
        return jsonify(
            {
                "status": "ok",
                "service": "esg-backend",
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "database": str(DB_PATH),
            }
        )

    @app.get("/api/monitoring")
    def get_monitoring() -> Any:
        with get_connection() as conn:
            metrics = conn.execute(
                """
                SELECT name, value, unit, status, target, period, accent
                FROM monitoring_metrics
                ORDER BY sort_order
                """
            ).fetchall()
            points = conn.execute(
                """
                SELECT name, status, top_percent, left_percent
                FROM monitoring_points
                ORDER BY id
                """
            ).fetchall()

        return jsonify(
            {
                "metrics": [dict(row) for row in metrics],
                "points": [dict(row) for row in points],
            }
        )

    @app.get("/api/compliance/standards")
    def get_standards() -> Any:
        with get_connection() as conn:
            standards = conn.execute(
                """
                SELECT name, selected_default
                FROM compliance_standards
                ORDER BY sort_order
                """
            ).fetchall()

        return jsonify(
            {
                "standards": [dict(row) for row in standards],
            }
        )

    @app.post("/api/compliance/generate")
    def generate_compliance_plan() -> Any:
        payload = request.get_json(silent=True) or {}
        selected_standards = payload.get("selected_standards") or []

        if not isinstance(selected_standards, list) or not selected_standards:
            return jsonify({"error": "selected_standards must be a non-empty list"}), 400

        with get_connection() as conn:
            base_details = conn.execute(
                """
                SELECT title, content
                FROM compliance_solutions
                ORDER BY sort_order
                """
            ).fetchall()
            conn.execute(
                """
                INSERT INTO compliance_requests (selected_standards, created_at)
                VALUES (?, ?)
                """,
                (
                    json.dumps(selected_standards, ensure_ascii=False),
                    utc_now(),
                ),
            )
            conn.commit()

        standards_text = "、".join(selected_standards[:3])
        if len(selected_standards) > 3:
            standards_text += "等准则"

        details = []
        for index, row in enumerate(base_details):
            content = row["content"]
            if index == 0:
                content = f"结合 {standards_text} 的约束，{content}"
            details.append({"title": row["title"], "content": content})

        return jsonify(
            {
                "path": "多准则交叉执行方案",
                "selected_standards": selected_standards,
                "details": details,
            }
        )

    @app.get("/api/suppliers")
    def get_suppliers() -> Any:
        with get_connection() as conn:
            suppliers = conn.execute(
                """
                SELECT id, name, product, price, action_label, category
                FROM suppliers
                ORDER BY id
                """
            ).fetchall()

        return jsonify({"suppliers": [dict(row) for row in suppliers]})

    @app.get("/api/orders")
    def get_orders() -> Any:
        status = request.args.get("status", "all").strip()
        with get_connection() as conn:
            query = base_order_query()
            params: list[Any] = []
            if status != "all":
                query += " WHERE o.status = ?"
                params.append(status)
            query += " ORDER BY o.created_at DESC, o.id DESC LIMIT 20"
            orders = conn.execute(query, params).fetchall()

        return jsonify({"orders": [dict(row) for row in orders]})

    @app.get("/api/orders/<int:order_id>")
    def get_order(order_id: int) -> Any:
        with get_connection() as conn:
            order = conn.execute(
                base_order_query() + " WHERE o.id = ?",
                (order_id,),
            ).fetchone()

        if order is None:
            return jsonify({"error": "order not found"}), 404

        return jsonify(dict(order))

    @app.post("/api/orders")
    def create_order() -> Any:
        payload = request.get_json(silent=True) or {}
        supplier_id = payload.get("supplier_id")
        project_name = (payload.get("project_name") or "").strip()
        contact_name = (payload.get("contact_name") or "").strip()
        quantity = payload.get("quantity")

        if not supplier_id:
            return jsonify({"error": "supplier_id is required"}), 400
        if not project_name:
            return jsonify({"error": "project_name is required"}), 400
        if not contact_name:
            return jsonify({"error": "contact_name is required"}), 400
        if quantity in (None, ""):
            return jsonify({"error": "quantity is required"}), 400

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return jsonify({"error": "quantity must be an integer"}), 400

        if quantity <= 0:
            return jsonify({"error": "quantity must be greater than 0"}), 400

        created_at = utc_now()
        with get_connection() as conn:
            supplier = conn.execute(
                """
                SELECT id, name, product, price, category
                FROM suppliers
                WHERE id = ?
                """,
                (supplier_id,),
            ).fetchone()

            if supplier is None:
                return jsonify({"error": "supplier not found"}), 404

            unit = category_to_unit(supplier["category"])
            status = "pending_payment"
            unit_price = parse_price_value(supplier["price"])
            total_amount = round(unit_price * quantity, 2)
            conn.execute(
                """
                INSERT INTO orders (
                    supplier_id,
                    project_name,
                    contact_name,
                    quantity,
                    unit,
                    unit_price,
                    total_amount,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    supplier_id,
                    project_name,
                    contact_name,
                    quantity,
                    unit,
                    unit_price,
                    total_amount,
                    status,
                    created_at,
                ),
            )
            order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

            order = conn.execute(
                base_order_query() + " WHERE o.id = ?",
                (order_id,),
            ).fetchone()

        return jsonify(dict(order)), 201

    @app.post("/api/orders/<int:order_id>/pay")
    def pay_order(order_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        payment_method = (payload.get("payment_method") or "").strip()
        payment_reference = (payload.get("payment_reference") or "").strip()

        if not payment_method:
            return jsonify({"error": "payment_method is required"}), 400
        if not payment_reference:
            return jsonify({"error": "payment_reference is required"}), 400

        with get_connection() as conn:
            order = conn.execute(
                base_order_query() + " WHERE o.id = ?",
                (order_id,),
            ).fetchone()

            if order is None:
                return jsonify({"error": "order not found"}), 404

            if order["status"] == "completed":
                return jsonify({"error": "order already completed"}), 400

            conn.execute(
                """
                UPDATE orders
                SET status = ?, payment_method = ?, payment_reference = ?, paid_at = ?
                WHERE id = ?
                """,
                ("completed", payment_method, payment_reference, utc_now(), order_id),
            )
            conn.commit()

            updated_order = conn.execute(
                base_order_query() + " WHERE o.id = ?",
                (order_id,),
            ).fetchone()

        return jsonify(dict(updated_order))

    @app.get("/api/social")
    def get_social() -> Any:
        with get_connection() as conn:
            scores = conn.execute(
                """
                SELECT label, score
                FROM social_scores
                ORDER BY sort_order
                """
            ).fetchall()
            summary = conn.execute(
                """
                SELECT summary_key, summary_value
                FROM social_summary
                """
            ).fetchall()
            alerts = conn.execute(
                """
                SELECT id, source, content, status, rejection_reason, rejected_at, created_at
                FROM social_alerts
                ORDER BY created_at DESC
                """
            ).fetchall()
            feedback = conn.execute(
                """
                SELECT description, location, status, resolution_note, created_at
                FROM feedback
                ORDER BY created_at DESC
                LIMIT 5
                """
            ).fetchall()

        return jsonify(
            {
                "scores": [dict(row) for row in scores],
                "summary": {row["summary_key"]: row["summary_value"] for row in summary},
                "alerts": [dict(row) for row in alerts],
                "feedback": [dict(row) for row in feedback],
            }
        )

    @app.post("/api/social/feedback")
    def create_feedback() -> Any:
        payload = request.get_json(silent=True) or {}
        description = (payload.get("description") or "").strip()
        location = (payload.get("location") or "未定位").strip()

        if not description:
            return jsonify({"error": "description is required"}), 400

        created_at = utc_now()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO feedback (description, location, status, resolution_note, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (description, location, "received", "已进入工单池，等待责任人处理。", created_at),
            )
            feedback_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

        return (
            jsonify(
                {
                    "id": feedback_id,
                    "description": description,
                    "location": location,
                    "status": "received",
                    "resolution_note": "已进入工单池，等待责任人处理。",
                    "created_at": created_at,
                }
            ),
            201,
        )

    @app.post("/api/social/alerts/<int:alert_id>/reject")
    def reject_social_alert(alert_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        rejection_reason = (payload.get("rejection_reason") or "").strip()

        if not rejection_reason:
            return jsonify({"error": "rejection_reason is required"}), 400

        rejected_at = utc_now()
        with get_connection() as conn:
            alert = conn.execute(
                """
                SELECT id, source, content, status, rejection_reason, rejected_at, created_at
                FROM social_alerts
                WHERE id = ?
                """,
                (alert_id,),
            ).fetchone()

            if alert is None:
                return jsonify({"error": "alert not found"}), 404

            conn.execute(
                """
                UPDATE social_alerts
                SET status = ?, rejection_reason = ?, rejected_at = ?
                WHERE id = ?
                """,
                ("rejected", rejection_reason, rejected_at, alert_id),
            )
            conn.commit()

            updated_alert = conn.execute(
                """
                SELECT id, source, content, status, rejection_reason, rejected_at, created_at
                FROM social_alerts
                WHERE id = ?
                """,
                (alert_id,),
            ).fetchone()

        return jsonify(dict(updated_alert))

    @app.get("/api/social/alerts/<int:alert_id>/download")
    def download_rectification_notice(alert_id: int) -> Any:
        with get_connection() as conn:
            alert = conn.execute(
                """
                SELECT id, source, content, status, created_at
                FROM social_alerts
                WHERE id = ?
                """,
                (alert_id,),
            ).fetchone()

        if alert is None:
            return jsonify({"error": "alert not found"}), 404

        document = build_rectification_notice(dict(alert))
        filename = f"rectification_notice_{alert_id}.txt"
        return send_file(
            BytesIO(document.encode("utf-8")),
            as_attachment=True,
            download_name=filename,
            mimetype="text/plain; charset=utf-8",
        )

    @app.get("/api/report")
    def export_report() -> Any:
        with get_connection() as conn:
            metrics = conn.execute(
                "SELECT name, value, unit, status FROM monitoring_metrics ORDER BY sort_order"
            ).fetchall()
            suppliers = conn.execute(
                "SELECT name, product, price FROM suppliers ORDER BY id"
            ).fetchall()
            feedback_stats = conn.execute(
                """
                SELECT status, COUNT(*) AS total
                FROM feedback
                GROUP BY status
                ORDER BY total DESC
                """
            ).fetchall()

        return jsonify(
            {
                "generated_at": utc_now(),
                "monitoring": [dict(row) for row in metrics],
                "suppliers": [dict(row) for row in suppliers],
                "feedback_stats": [dict(row) for row in feedback_stats],
            }
        )

    return app


def get_connection() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS monitoring_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                value TEXT NOT NULL,
                unit TEXT NOT NULL,
                status TEXT NOT NULL,
                target TEXT,
                period TEXT,
                accent TEXT NOT NULL,
                sort_order INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monitoring_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                top_percent TEXT NOT NULL,
                left_percent TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS compliance_standards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                selected_default INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS compliance_solutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                sort_order INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS compliance_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                selected_standards TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                product TEXT NOT NULL,
                price TEXT NOT NULL,
                action_label TEXT NOT NULL,
                category TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier_id INTEGER NOT NULL,
                project_name TEXT NOT NULL,
                contact_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit TEXT NOT NULL,
                unit_price REAL NOT NULL DEFAULT 0,
                total_amount REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                payment_method TEXT,
                payment_reference TEXT,
                paid_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
            );

            CREATE TABLE IF NOT EXISTS social_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                score REAL NOT NULL,
                sort_order INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS social_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary_key TEXT NOT NULL UNIQUE,
                summary_value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS social_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                rejection_reason TEXT,
                rejected_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                location TEXT NOT NULL,
                status TEXT NOT NULL,
                resolution_note TEXT,
                created_at TEXT NOT NULL
            );
            """
        )

        migrate_orders_table(conn)
        migrate_social_alerts_table(conn)

        if conn.execute("SELECT COUNT(*) FROM monitoring_metrics").fetchone()[0] == 0:
            seed_database(conn)
        conn.commit()


def seed_database(conn: sqlite3.Connection) -> None:
    conn.executemany(
        """
        INSERT INTO monitoring_metrics (name, value, unit, status, target, period, accent, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("PM 2.5 实时监测", "45.2", "μg/m³", "Safe", None, "实时", "amber", 1),
            ("总电能消耗", "14,208", "kWh", "30-Day", None, "30-Day", "emerald", 2),
            ("水资源消耗", "12.5", "kM³", "Target: 15.0", "15.0", "本月", "blue", 3),
        ],
    )

    conn.executemany(
        """
        INSERT INTO monitoring_points (name, status, top_percent, left_percent)
        VALUES (?, ?, ?, ?)
        """,
        [
            ("扬尘监测点 A", "normal", "25%", "33%"),
            ("噪音监测点 B", "warning", "50%", "67%"),
        ],
    )

    conn.executemany(
        """
        INSERT INTO compliance_standards (name, selected_default, sort_order)
        VALUES (?, ?, ?)
        """,
        [
            ("GRI 国际标准", 0, 1),
            ("世行 ESF 框架", 0, 2),
            ("东道国法律", 0, 3),
            ("T/CHINCA 指引", 1, 4),
            ("ISSB 准则", 0, 5),
            ("SASB 行业标准", 0, 6),
            ("赤道原则 IV", 0, 7),
            ("UN 全球契约", 0, 8),
            ("ISO 14064", 0, 9),
            ("TCFD 气候披露", 0, 10),
        ],
    )

    conn.executemany(
        """
        INSERT INTO compliance_solutions (title, content, sort_order)
        VALUES (?, ?, ?)
        """,
        [
            ("施工现场准入", "需额外增加施工前的水生生物多样性评估及土壤修复预案。", 1),
            ("碳核算口径", "采用《指引》附录 A 的活动数据法进行实时核算，并保持对披露口径的一致性。", 2),
            ("劳工管理", "需在下周前补全匿名申诉渠道的物理标识及跨文化福利公示。", 3),
        ],
    )

    conn.executemany(
        """
        INSERT INTO suppliers (name, product, price, action_label, category)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("海螺低碳水泥", "LC-425系列", "¥420/t", "直接采购", "建材"),
            ("三一节能机械", "电动液压挖掘机", "¥145w", "直接采购", "设备"),
            ("远景能源", "2.5MW 光伏路灯", "¥1,240", "直接采购", "能源"),
        ],
    )

    conn.executemany(
        """
        INSERT INTO orders (
            supplier_id, project_name, contact_name, quantity, unit,
            unit_price, total_amount, status, payment_method, payment_reference, paid_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1, "雅加达园区一期", "王工", 120, "吨", 420.0, 50400.0,
                "completed", "bank_transfer", "BANK-20260414001", utc_now(), utc_now()
            ),
            (
                3, "北区道路照明改造", "李经理", 40, "套", 1240.0, 49600.0,
                "pending_payment", None, None, None, utc_now()
            ),
        ],
    )

    conn.executemany(
        """
        INSERT INTO social_scores (label, score, sort_order)
        VALUES (?, ?, ?)
        """,
        [
            ("工作环境安全", 4.8, 1),
            ("薪资福利及时发放", 4.9, 2),
            ("跨文化培训参与度", 4.2, 3),
            ("心理健康压力指数", 2.1, 4),
        ],
    )

    conn.executemany(
        """
        INSERT INTO social_summary (summary_key, summary_value)
        VALUES (?, ?)
        """,
        [
            ("valid_surveys", "1452"),
            ("latest_status", "感谢！道路坑洼已修补。"),
        ],
    )

    conn.execute(
        """
        INSERT INTO social_alerts (source, content, status, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            "河畔居民区",
            "投诉内容：凌晨 2 点的打桩作业噪音严重超标，且现场未安装粉尘喷淋。",
            "open",
            utc_now(),
        ),
    )

    conn.executemany(
        """
        INSERT INTO feedback (description, location, status, resolution_note, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("施工道路坑洼影响车辆通行", "社区道路入口", "closed", "道路已完成修补。", utc_now()),
            ("夜间噪音较大，希望调整施工时段", "河畔居民区", "received", "已进入工单池，等待责任人处理。", utc_now()),
        ],
    )


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def category_to_unit(category: str) -> str:
    mapping = {
        "建材": "吨",
        "设备": "台",
        "能源": "套",
    }
    return mapping.get(category, "件")


def parse_price_value(raw_price: str) -> float:
    text = raw_price.strip().replace("¥", "").replace(",", "")
    if "/" in text:
        text = text.split("/", 1)[0]
    multiplier = 1.0
    if text.endswith("w"):
        text = text[:-1]
        multiplier = 10000.0
    try:
        return round(float(text) * multiplier, 2)
    except ValueError:
        return 0.0


def migrate_orders_table(conn: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()
    }
    if not existing_columns:
        return

    if "unit_price" not in existing_columns:
        conn.execute("ALTER TABLE orders ADD COLUMN unit_price REAL NOT NULL DEFAULT 0")
    if "total_amount" not in existing_columns:
        conn.execute("ALTER TABLE orders ADD COLUMN total_amount REAL NOT NULL DEFAULT 0")
    if "payment_method" not in existing_columns:
        conn.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT")
    if "payment_reference" not in existing_columns:
        conn.execute("ALTER TABLE orders ADD COLUMN payment_reference TEXT")
    if "paid_at" not in existing_columns:
        conn.execute("ALTER TABLE orders ADD COLUMN paid_at TEXT")

    conn.execute(
        """
        UPDATE orders
        SET status = 'completed'
        WHERE status IN ('approved')
        """
    )
    conn.execute(
        """
        UPDATE orders
        SET status = 'pending_payment'
        WHERE status IN ('pending_review')
        """
    )
    conn.execute(
        """
        UPDATE orders
        SET unit_price = (
            SELECT COALESCE(
                CASE
                    WHEN REPLACE(REPLACE(REPLACE(s.price, '¥', ''), ',', ''), '/t', '') LIKE '%w'
                    THEN CAST(REPLACE(REPLACE(REPLACE(REPLACE(s.price, '¥', ''), ',', ''), '/t', ''), 'w', '') AS REAL) * 10000
                    ELSE CAST(REPLACE(REPLACE(REPLACE(s.price, '¥', ''), ',', ''), '/t', '') AS REAL)
                END,
                0
            )
            FROM suppliers s
            WHERE s.id = orders.supplier_id
        )
        WHERE unit_price = 0
        """
    )
    conn.execute(
        """
        UPDATE orders
        SET total_amount = ROUND(quantity * unit_price, 2)
        WHERE total_amount = 0
        """
    )
    conn.execute(
        """
        UPDATE orders
        SET paid_at = created_at
        WHERE status = 'completed' AND paid_at IS NULL
        """
    )


def migrate_social_alerts_table(conn: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(social_alerts)").fetchall()
    }
    if not existing_columns:
        return

    if "rejection_reason" not in existing_columns:
        conn.execute("ALTER TABLE social_alerts ADD COLUMN rejection_reason TEXT")
    if "rejected_at" not in existing_columns:
        conn.execute("ALTER TABLE social_alerts ADD COLUMN rejected_at TEXT")


def base_order_query() -> str:
    return """
        SELECT
            o.id,
            o.project_name,
            o.contact_name,
            o.quantity,
            o.unit,
            o.unit_price,
            o.total_amount,
            o.status,
            o.payment_method,
            o.payment_reference,
            o.paid_at,
            o.created_at,
            s.id AS supplier_id,
            s.name AS supplier_name,
            s.product AS supplier_product,
            s.price AS supplier_price,
            s.category AS supplier_category
        FROM orders o
        JOIN suppliers s ON s.id = o.supplier_id
    """


def build_rectification_notice(alert: dict[str, Any]) -> str:
    created_at = alert.get("created_at") or utc_now()
    return f"""海外项目社区纠纷整改通知单

单号: ESG-SOC-{alert['id']:04d}
生成时间: {utc_now()}
预警来源: {alert['source']}
预警时间: {created_at}
当前状态: {alert['status']}

一、问题描述
{alert['content']}

二、整改要求
1. 立即核查投诉涉及的施工区域、施工班组和作业时间段。
2. 24 小时内完成噪音、扬尘和现场围挡措施复核，并形成照片记录。
3. 48 小时内提交整改闭环说明，明确责任人、整改完成时间和复发预防措施。
4. 如涉及夜间施工，需同步补充东道国许可文件及社区告知记录。

三、责任分工
责任部门: 项目工程部 / ESG 管理专员 / 社区沟通窗口
升级要求: 如 48 小时内未完成整改，升级至项目负责人专项督办。

四、回执要求
请责任部门在整改完成后，将结果回传至 ESG 数字化管理平台并归档。
"""


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
