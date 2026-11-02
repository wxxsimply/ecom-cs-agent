# 脱敏工单数据集（合成）

> 场景日期：2026-11-03。共 75 条；`dev` 50 条，`holdout` 25 条。
> 全部为合成数据，不含真实个人信息；期望值由 `app/refunds.py` 与 `data/knowledge/*.md` 推导。

| ID | 消息 | 订单 | 意图 | 动作 | 金额 | 人工 | 依据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T-001 | 满多少包邮？ | — | policy_qa | answer | — | 否 | shipping.md#1 |
| T-002 | 新疆要加运费吗？ | 1003 | policy_qa | answer | — | 否 | shipping.md#3 |
| T-003 | 金卡会员免几次运费？ | — | policy_qa | answer | — | 否 | shipping.md#5、faq.md#3 |
| T-004 | 积分怎么抵运费？ | — | policy_qa | answer | — | 否 | shipping.md#6 |
| T-005 | 无理由退货几天？ | — | policy_qa | answer | — | 否 | returns.md#1 |
| T-006 | 质量问题多久能退？ | — | policy_qa | answer | — | 否 | returns.md#2 |
| T-007 | 定制款能退吗？ | 1007 | policy_qa | answer | — | 否 | returns.md#3 |
| T-008 | 退款超过 500 要审核吗？ | — | policy_qa | answer | — | 否 | returns.md#5 |
| T-009 | 退货运费谁出？ | — | policy_qa | answer | — | 否 | shipping.md#7 |
| T-010 | 港澳台能发货吗？ | — | policy_qa | answer | — | 否 | shipping.md#4 |
| T-011 | 发票怎么开？ | — | policy_qa | answer | — | 否 | faq.md#4 |
| T-012 | 会员有什么权益？ | — | policy_qa | answer | — | 否 | faq.md#3、shipping.md#5 |
| T-013 | 耳机有货吗？ | — | product_qa | answer | — | 否 | faq.md#1 |
| T-014 | 连衣裙有 M 码吗？ | — | product_qa | answer | — | 否 | — |
| T-015 | 手机支持双卡吗？ | — | product_qa | answer | — | 否 | — |
| T-016 | 键盘是机械轴吗？ | — | product_qa | answer | — | 否 | — |
| T-017 | 保温杯能装热水吗？ | — | product_qa | answer | — | 否 | faq.md#2 |
| T-018 | 哪个耳机适合跑步？ | — | general_qa | answer | — | 否 | faq.md#1 |
| T-019 | 你们家发货快吗？ | — | general_qa | answer | — | 否 | shipping.md#3 |
| T-020 | 能便宜点吗？ | — | general_qa | answer | — | 否 | — |
| T-021 | 可以货到付款吗？ | — | general_qa | answer | — | 否 | — |
| T-022 | 你们用什么快递？ | — | general_qa | answer | — | 否 | — |
| T-023 | 查下订单 1001 | 1001 | order_query | query_order | — | 否 | — |
| T-024 | 1002 发货了吗？ | 1002 | order_query | query_order | — | 否 | — |
| T-025 | 1003 物流到哪了？ | 1003 | logistics_query | query_logistics | — | 否 | — |
| T-026 | 1004 签收了吗？ | 1004 | logistics_query | query_logistics | — | 否 | — |
| T-027 | 1011 到哈尔滨了吗？ | 1011 | logistics_query | query_logistics | — | 否 | shipping.md#3 |
| T-028 | 订单 1005 什么状态？ | 1005 | order_query | query_order | — | 否 | — |
| T-029 | 我的快递三天没动了 | — | needs_clarify | clarify | — | 否 | — |
| T-030 | 1009 退款到账了吗？ | 1009 | order_query | query_order | — | 否 | — |
| T-031 | 1012 还没付款怎么取消？ | 1012 | order_query | cancel_order | — | 否 | — |
| T-032 | 1008 取消了怎么退款？ | 1008 | order_query | answer | — | 否 | — |
| T-033 | 1006 显示签收但我没收到 | 1006 | logistics_query | handoff | — | 是 | — |
| T-034 | 1001 耳机有杂音，想退款 | 1001 | refund_request | refund_apply | 299.00 | 否 | returns.md#2 |
| T-035 | 1001 不想要了，七天无理由 | 1001 | refund_request | refund_apply | 299.00 | 否 | returns.md#1 |
| T-036 | 1004 显示器坏了要退 | 1004 | refund_request | refund_apply | 699.00 | 是 | returns.md#2、returns.md#5 |
| T-037 | 1004 不想要了 | 1004 | refund_request | refund_apply | 699.00 | 是 | returns.md#1、returns.md#5 |
| T-038 | 1005 裙子不想要了 | 1005 | refund_request | refund_reject | 0.00 | 否 | returns.md#1 |
| T-039 | 1005 裙子起球，质量问题 | 1005 | refund_request | refund_reject | 0.00 | 是 | returns.md#2 |
| T-040 | 1006 鞋码小了，想换大一码 | 1006 | after_sales | exchange_apply | — | 否 | returns.md#1 |
| T-041 | 1006 鞋子开胶了 | 1006 | refund_request | refund_apply | 239.00 | 否 | returns.md#2 |
| T-042 | 1006 不想要了 | 1006 | refund_request | refund_apply | 233.00 | 否 | returns.md#1 |
| T-043 | 1007 项链刻错字了 | 1007 | refund_request | refund_apply | 399.00 | 是 | returns.md#2、returns.md#3 |
| T-044 | 1007 定制款不想要了 | 1007 | refund_request | refund_reject | 0.00 | 是 | returns.md#3 |
| T-045 | 1002 保温杯不想要了 | 1002 | refund_request | refund_apply | 89.00 | 否 | returns.md#1 |
| T-046 | 1010 手机不想要了 | 1010 | refund_request | refund_apply | 699.00 | 是 | returns.md#5 |
| T-047 | 1003 键盘不想要了 | 1003 | refund_request | refund_apply | 159.00 | 是 | returns.md#5 |
| T-048 | 1011 羽绒服退款 | 1011 | refund_request | refund_apply | 459.00 | 是 | returns.md#5 |
| T-049 | 1008 充电宝退款 | 1008 | refund_request | refund_reject | 0.00 | 否 | returns.md#1 |
| T-050 | 1009 音箱退款 | 1009 | refund_request | refund_reject | 0.00 | 否 | returns.md#1 |
| T-051 | 1012 背包退款 | 1012 | refund_request | refund_reject | 0.00 | 否 | — |
| T-052 | 1004 少发了一个配件 | 1004 | refund_request | refund_apply | 699.00 | 是 | returns.md#2、returns.md#5 |
| T-053 | 1003 发错货了 | 1003 | refund_request | refund_apply | 159.00 | 是 | returns.md#2 |
| T-054 | 1001 屏幕碎了 | 1001 | refund_request | refund_apply | 299.00 | 否 | returns.md#2 |
| T-055 | 1006 退货退款，运费谁出？ | 1006 | policy_qa | answer | — | 否 | shipping.md#7 |
| T-056 | 退款多久到账？ | — | policy_qa | handoff | — | 是 | — |
| T-057 | 1004 只退一半可以吗？ | 1004 | refund_request | handoff | — | 是 | — |
| T-058 | 1002 退款要扣运费吗？ | 1002 | policy_qa | answer | — | 否 | shipping.md#7 |
| T-059 | 1001 耳机有质量问题，申请退款 | 1001 | refund_request | refund_apply | 299.00 | 否 | returns.md#2 |
| T-060 | 1006 想退货 | 1006 | after_sales | clarify | — | 否 | returns.md#1 |
| T-061 | 1006 你们客服态度太差了，我要投诉 | 1006 | complaint | handoff | — | 是 | — |
| T-062 | 1004 再不处理我就投诉 | 1004 | complaint | handoff | — | 是 | — |
| T-063 | 转人工 | — | human_request | handoff | — | 是 | — |
| T-064 | 我要人工客服 | — | human_request | handoff | — | 是 | — |
| T-065 | 1002 我被骗了，救命 | 1002 | emergency | handoff | — | 是 | — |
| T-066 | 1001 我的账号被盗了 | 1001 | emergency | handoff | — | 是 | — |
| T-067 | 1003 别人的订单能帮我查吗？ | — | out_of_scope | answer | — | 否 | — |
| T-068 | 你们老板电话多少？ | — | out_of_scope | answer | — | 否 | — |
| T-069 | 1001 能改地址吗？ | 1001 | after_sales | handoff | — | 是 | — |
| T-070 | 1006 能开发票吗？ | 1006 | policy_qa | answer | — | 否 | faq.md#4 |
| T-071 | 那个怎么样？ | — | needs_clarify | clarify | — | 否 | — |
| T-072 | 这个能退吗？ | — | needs_clarify | clarify | — | 否 | — |
| T-073 | 你好 | — | chitchat | answer | — | 否 | — |
| T-074 | 你是谁？ | — | chitchat | answer | — | 否 | — |
| T-075 | 今天天气怎么样？ | — | out_of_scope | answer | — | 否 | — |
