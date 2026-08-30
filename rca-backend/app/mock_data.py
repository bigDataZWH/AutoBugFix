from __future__ import annotations


SAMPLE_TICKETS = [
    {
        "ticket_id": "INC-2025-018832",
        "title": "下单高峰期订单超卖：库存为负",
        "description": "大促期间库存扣减出现超卖，扣减后库存为负数，触发资损告警。堆栈指向 OrderLockService.acquire Redis 分布式锁超时。",
        "root_cause": "OrderLockService.acquire 使用单一 Redis key 做全量锁，高并发下锁竞争排队超时，部分请求未持锁即扣减，导致库存超卖。根本原因是锁粒度过粗+缺少兜底校验。",
        "fix_code": "将全量锁改为按 skuId 分段锁；扣减后增加库存非负校验；引入 Lua 原子扣减脚本。",
        "microservice": "order-center",
        "module": "OrderLockService",
        "error_code": "STOCK_NEGATIVE",
        "severity": "P1",
    },
    {
        "ticket_id": "INC-2025-014221",
        "title": "支付回调重复入账",
        "description": "支付网关重试回调导致订单被重复入账，账实不符。堆栈指向 PaymentCallbackHandler.handle。",
        "root_cause": "PaymentCallbackHandler 未做幂等校验，直接依据回调状态更新订单为已支付，重试回调重复入账。",
        "fix_code": "引入幂等表(回调流水号唯一索引)；处理前校验流水号是否已处理；状态机校验只允许待支付->已支付。",
        "microservice": "payment-gateway",
        "module": "PaymentCallbackHandler",
        "error_code": "DUPLICATE_ENTRY",
        "severity": "P1",
    },
    {
        "ticket_id": "INC-2025-009876",
        "title": "连接池耗尽导致服务雪崩",
        "description": "依赖的下游慢调用导致 Jedis 连接池耗尽，上游线程全部阻塞，服务雪崩。",
        "root_cause": "JedisPool 未配置 maxWait，慢调用时连接获取无限等待，线程池被占满；缺少熔断与超时。",
        "fix_code": "配置 maxWaitMillis 与 borrowTimeout；引入 Sentinel 熔断降级；对下游调用加 RT 超时。",
        "microservice": "inventory-service",
        "module": "RedisConnectionManager",
        "error_code": "POOL_EXHAUSTED",
        "severity": "P0",
    },
    {
        "ticket_id": "INC-2024-031120",
        "title": "订单状态查询慢导致网关 5xx",
        "description": "订单列表查询响应时间从 50ms 飙升到 3s，网关超时返回 5xx。",
        "root_cause": "OrderQueryService.listOrders 缺少分页深度限制且未走索引，深分页触发全表扫描。",
        "fix_code": "限制最大分页深度；游标分页替代 offset；为 (user_id, create_time) 建联合索引。",
        "microservice": "order-center",
        "module": "OrderQueryService",
        "error_code": "SLOW_QUERY",
        "severity": "P2",
    },
    {
        "ticket_id": "INC-2024-028775",
        "title": "定时任务并发执行导致重复发货",
        "description": "发货定时任务在多实例部署时被同时触发，重复发货。",
        "root_cause": "ShipmentScheduler 定时任务未加分布式锁，多实例并发执行。",
        "fix_code": "引入 Redis 分布式锁抢占式执行；ShedLock 注解保证单实例执行；执行前校验发货状态。",
        "microservice": "shipment-service",
        "module": "ShipmentScheduler",
        "error_code": "DUPLICATE_SHIPMENT",
        "severity": "P1",
    },
    {
        "ticket_id": "INC-2025-021034",
        "title": "优惠券叠加使用导致 0 元订单",
        "description": "前端重复提交叠加多张满减券，最终订单金额为 0 甚至为负。",
        "root_cause": "CouponService.apply 缺少幂等与叠加校验，同一订单可叠加同一批次券；金额未做下限保护。",
        "fix_code": "幂等键去重；券叠加规则校验；订单金额下限保护(>=0.01)；风控二次校验。",
        "microservice": "promotion-service",
        "module": "CouponService",
        "error_code": "AMOUNT_INVALID",
        "severity": "P1",
    },
    {
        "ticket_id": "INC-2025-007654",
        "title": "短信网关限流未生效导致大量堆积",
        "description": "营销短信发送未限流，瞬间打满下游网关配额，全部失败。",
        "root_cause": "SmsSender 未接入限流，突发流量直打下游；缺少令牌桶与降级队列。",
        "fix_code": "接入令牌桶限流；超量进入 Kafka 削峰；失败重试退避；配额预警。",
        "microservice": "notify-service",
        "module": "SmsSender",
        "error_code": "RATE_LIMIT_MISSING",
        "severity": "P2",
    },
]


MOCK_OPENCODE_OUTPUT = {
    "repo": "microservice-order-center",
    "branch": "release/2026.08",
    "commit": "a1b2c3d",
    "symbols": [
        {"id": "sym:OrderController:createOrder", "type": "method", "file": "src/main/java/.../OrderController.java", "line": 64, "signature": "createOrder(OrderReq):Result<Long>", "class": "OrderController"},
        {"id": "sym:OrderService:submit", "type": "method", "file": "src/main/java/.../OrderService.java", "line": 88, "signature": "submit(OrderReq):Long", "class": "OrderService"},
        {"id": "sym:OrderLockService:acquire", "type": "method", "file": "src/main/java/.../OrderLockService.java", "line": 127, "signature": "acquire(skuId):boolean", "class": "OrderLockService"},
        {"id": "sym:StockService:deduct", "type": "method", "file": "src/main/java/.../StockService.java", "line": 203, "signature": "deduct(skuId,qty):boolean", "class": "StockService"},
        {"id": "sym:RedisClient:setnx", "type": "method", "file": "src/main/java/.../RedisClient.java", "line": 45, "signature": "setnx(key,ttl):boolean", "class": "RedisClient"},
    ],
    "call_edges": [
        {"src": "sym:OrderController:createOrder", "dst": "sym:OrderService:submit", "kind": "call", "file": "OrderController.java", "line": 67},
        {"src": "sym:OrderService:submit", "dst": "sym:OrderLockService:acquire", "kind": "call", "file": "OrderService.java", "line": 91},
        {"src": "sym:OrderService:submit", "dst": "sym:StockService:deduct", "kind": "call", "file": "OrderService.java", "line": 95},
        {"src": "sym:OrderLockService:acquire", "dst": "sym:RedisClient:setnx", "kind": "call", "file": "OrderLockService.java", "line": 131},
    ],
    "data_flows": [
        {"var": "skuId", "def": "sym:OrderController:createOrder:line64", "uses": ["sym:OrderLockService:acquire:line127", "sym:StockService:deduct:line203"], "taint": "user_input"},
    ],
    "hotspots": [
        {"symbol": "sym:OrderLockService:acquire", "line": 127, "reason": "sync_block+resource_acquire+single_key_lock", "score": 0.94},
        {"symbol": "sym:StockService:deduct", "line": 203, "reason": "no_guard_after_lock", "score": 0.86},
    ],
}


SAMPLE_PRACTICES = [
    {
        "title": "分布式锁分段",
        "content": "对热点资源(库存)按 hash 分段加锁，降低单 key 竞争；配合 Lua 脚本保证扣减原子性，避免锁内嵌套远程调用。",
        "source": "阿里《亿级流量》/ 华为云分布式锁最佳实践",
    },
    {
        "title": "幂等三件套",
        "content": "唯一索引(流水号) + 状态机校验 + 防重 Token，三重保障接口幂等，适用于支付回调、下单、发货等场景。",
        "source": "支付宝幂等规范 / 分布式系统设计模式",
    },
    {
        "title": "连接池容量公式",
        "content": "pool_size = (RT_ms * QPS) / 1000 * 冗余系数(1.2-1.5)；必配 maxWait 与 borrowTimeout，避免无限等待拖垮线程池。",
        "source": "HikariCP Wiki / JedisPool 容量规划",
    },
    {
        "title": "熔断降级三态",
        "content": "closed->open->half-open 状态机，慢调用/异常达阈值即熔断，half-open 探测恢复，保护下游与自身。",
        "source": "Sentinel / Resilience4j 文档",
    },
    {
        "title": "深分页优化",
        "content": "游标分页(cursor)替代 offset，配合联合索引避免回表；限制最大分页深度，防止深翻页全表扫描。",
        "source": "MySQL 高性能 / ShardingSphere 分页实践",
    },
]
