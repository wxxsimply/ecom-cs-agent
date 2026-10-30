from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal, init_db, utcnow
from app.models import Order, OrderItem, Shipment, User


ORDER_SEED = [
    # id, title, status, paid, goods, shipping, region, delivered_days_ago, item_category, returnable
    ("1001", "无线耳机", "delivered", 299.0, 299.0, 0.0, "上海", 3, "digital", True),
    ("1002", "保温杯", "paid", 89.0, 83.0, 6.0, "上海", None, "home", True),
    ("1003", "机械键盘", "shipped", 159.0, 153.0, 6.0, "新疆", None, "digital", True),
    ("1004", "高端显示器", "delivered", 699.0, 699.0, 0.0, "上海", 2, "digital", True),
    ("1005", "夏季连衣裙", "delivered", 129.0, 129.0, 0.0, "杭州", 20, "clothing", True),
    ("1006", "运动鞋", "delivered", 239.0, 233.0, 6.0, "上海", 6, "shoes", True),
    ("1007", "定制刻字项链", "delivered", 399.0, 399.0, 0.0, "上海", 4, "custom", False),
    ("1008", "便携充电宝", "cancelled", 99.0, 99.0, 0.0, "上海", None, "digital", True),
    ("1009", "蓝牙音箱", "refunded", 199.0, 199.0, 0.0, "上海", 12, "digital", True),
    ("1010", "旗舰手机", "paid", 699.0, 693.0, 6.0, "上海", None, "digital", True),
    ("1011", "冬季羽绒服", "shipped", 459.0, 453.0, 6.0, "哈尔滨", None, "clothing", True),
    ("1012", "预售款背包", "pending_payment", 159.0, 153.0, 6.0, "上海", None, "bag", True),
]


DELIVERED_SHIPMENTS = {
    "1001": ("顺丰", "SF10012026"),
    "1004": ("京东物流", "JD10042026"),
    "1005": ("中通", "ZT10052026"),
    "1006": ("圆通", "YT10062026"),
    "1007": ("顺丰", "SF10072026"),
    "1009": ("中通", "ZT10092026"),
}


def _delivered_events(delivered_at) -> list[dict]:
    """生成 5 条可追溯的签收轨迹，最后一条必须是已签收。"""
    points = [
        (4, "商家已发货"),
        (3, "承运商已揽收"),
        (2, "到达转运中心"),
        (1, "派送中"),
        (0, "已签收，签收人：本人"),
    ]
    return [
        {
            "time": (delivered_at - timedelta(days=offset)).strftime("%Y-%m-%d %H:%M"),
            "text": text,
        }
        for offset, text in points
    ]


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        user = db.get(User, "u_demo")
        if user is None:
            db.add(User(id="u_demo", external_id="u_demo", nickname="小猫用户", member_level="gold", points=1200))
        else:
            user.nickname = "小猫用户"
            user.member_level = "gold"
            user.points = max(user.points or 0, 1200)

        for idx, (oid, title, status, paid, goods, shipping, region, delivered_days, category, returnable) in enumerate(ORDER_SEED):
            existing = db.get(Order, oid)
            if existing is None:
                delivered_at = utcnow() - timedelta(days=delivered_days) if delivered_days is not None else None
                db.add(
                    Order(
                        id=oid,
                        order_no=oid,
                        user_id="u_demo",
                        title=title,
                        status=status,
                        paid_amount=paid,
                        goods_paid=goods,
                        shipping_fee=shipping,
                        points_used=0,
                        refunded_amount=paid if status == "refunded" else 0.0,
                        region=region,
                        delivered_at=delivered_at,
                        created_at=utcnow() - timedelta(days=(delivered_days + 6 if delivered_days is not None else 2)),
                        items=[
                            OrderItem(
                                product_id=f"p_{oid}",
                                title=title,
                                qty=1,
                                unit_price=goods,
                                category=category,
                                returnable=returnable,
                            )
                        ],
                    )
                )
        db.commit()

        shipment_seed = {
            "1003": ("顺丰", "SF1234567890", "运输中", '[{"time":"2026-10-28 09:00","text":"已揽收"},{"time":"2026-10-28 18:00","text":"到达上海转运中心"},{"time":"2026-10-29 08:00","text":"派送中"}]'),
            "1011": ("京东物流", "JD9876543210", "已发货", '[{"time":"2026-10-29 10:00","text":"已出库"},{"time":"2026-10-29 20:00","text":"发往哈尔滨"}]'),
        }
        for oid, (carrier, tracking, status, events) in shipment_seed.items():
            exists = db.scalar(select(Shipment).where(Shipment.order_id == oid))
            if exists is None:
                db.add(Shipment(order_id=oid, carrier=carrier, tracking_no=tracking, status=status, events_json=events))

        # 已签收订单必须有完整历史轨迹，工作过程可追溯。
        for oid, (carrier, tracking) in DELIVERED_SHIPMENTS.items():
            order = db.get(Order, oid)
            if order is None or order.delivered_at is None:
                continue
            exists = db.scalar(select(Shipment).where(Shipment.order_id == oid))
            if exists is None:
                db.add(
                    Shipment(
                        order_id=oid,
                        carrier=carrier,
                        tracking_no=tracking,
                        status="已签收",
                        events_json=json.dumps(_delivered_events(order.delivered_at), ensure_ascii=False),
                    )
                )
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    print("seeded")
