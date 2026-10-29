from __future__ import annotations

from datetime import timedelta

from app.db import SessionLocal, init_db, utcnow
from app.models import Order, OrderItem, Shipment, User


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.get(User, "u_demo") is None:
            db.add(User(id="u_demo", external_id="u_demo", nickname="小猫用户", member_level="gold", points=1200))

        if db.get(Order, "1001") is None:
            db.add(
                Order(
                    id="1001",
                    order_no="1001",
                    user_id="u_demo",
                    title="无线耳机",
                    status="delivered",
                    paid_amount=299.0,
                    goods_paid=299.0,
                    shipping_fee=0.0,
                    points_used=0,
                    region="上海",
                    delivered_at=utcnow() - timedelta(days=3),
                    items=[OrderItem(product_id="p_earphone", title="无线耳机", qty=1, unit_price=299.0, category="digital", returnable=True)],
                )
            )
        if db.get(Order, "1002") is None:
            db.add(
                Order(
                    id="1002",
                    order_no="1002",
                    user_id="u_demo",
                    title="保温杯",
                    status="paid",
                    paid_amount=89.0,
                    goods_paid=83.0,
                    shipping_fee=6.0,
                    points_used=0,
                    region="上海",
                    items=[OrderItem(product_id="p_cup", title="保温杯", qty=1, unit_price=83.0, category="home", returnable=True)],
                )
            )
        if db.get(Order, "1003") is None:
            db.add(
                Order(
                    id="1003",
                    order_no="1003",
                    user_id="u_demo",
                    title="机械键盘",
                    status="shipped",
                    paid_amount=159.0,
                    goods_paid=153.0,
                    shipping_fee=6.0,
                    points_used=0,
                    region="新疆",
                    items=[OrderItem(product_id="p_keyboard", title="机械键盘", qty=1, unit_price=153.0, category="digital", returnable=True)],
                )
            )
        if db.get(Order, "1004") is None:
            db.add(
                Order(
                    id="1004",
                    order_no="1004",
                    user_id="u_demo",
                    title="高端显示器",
                    status="delivered",
                    paid_amount=699.0,
                    goods_paid=699.0,
                    shipping_fee=0.0,
                    points_used=0,
                    region="上海",
                    delivered_at=utcnow() - timedelta(days=2),
                    items=[OrderItem(product_id="p_monitor", title="高端显示器", qty=1, unit_price=699.0, category="digital", returnable=True)],
                )
            )
        db.commit()

        if db.get(Shipment, 1) is None:
            db.add(
                Shipment(
                    order_id="1003",
                    carrier="顺丰",
                    tracking_no="SF1234567890",
                    status="运输中",
                    events_json='[{"time":"2026-10-28 09:00","text":"已揽收"},{"time":"2026-10-28 18:00","text":"到达上海转运中心"},{"time":"2026-10-29 08:00","text":"派送中"}]',
                )
            )
            db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    print("seeded")
