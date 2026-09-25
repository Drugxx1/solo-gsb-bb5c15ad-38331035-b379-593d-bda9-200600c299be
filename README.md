# 冷库温度越限复盘 API

基于 **Python 3.11 + FastAPI** 的冷库温度越限复盘服务。接收同一冷库的**带时区**温度读数、温度上限、最大采样间隔、最短报警时长与维护豁免时段，在有效相邻读数间做**线性插值**，精确求出温度超过上限的连续区间；采样断档标记为**未知**且越限区间**不跨越**断档；扣除维护豁免时段后，逐段按时长筛出报警。

## 一、快速开始（单条启动命令）

### 方式 A：Docker Compose（推荐）

依赖安装在镜像构建时完成，初始化与服务启动由入口脚本统一完成，一条命令即可：

```bash
docker compose up --build
```

启动后接口地址：

- 服务地址：`http://localhost:8000`
- 健康检查：`GET http://localhost:8000/health`
- 复盘接口：`POST http://localhost:8000/api/v1/cold-storage/temperature/review`
- 交互式文档（Swagger UI）：`http://localhost:8000/docs`
- OpenAPI Schema：`http://localhost:8000/openapi.json`

### 方式 B：本地直接运行

同样是一条命令，脚本会自动创建虚拟环境、安装依赖并启动：

```bash
./docker/entrypoint.sh
```

## 二、必要配置

配置通过环境变量提供（`docker-compose.yml` 中可改）：

| 环境变量  | 默认值    | 说明           |
| --------- | --------- | -------------- |
| `APP_HOST`| `0.0.0.0` | 监听地址       |
| `APP_PORT`| `8000`    | 容器内监听端口 |

对外访问端口在 `docker-compose.yml` 的 `ports` 中配置，格式为 `"宿主机端口:容器端口"`（默认 `"8000:8000"`）。若修改容器侧端口，请同步修改 `APP_PORT` 与端口映射。

所有业务参数（温度上限、最大采样间隔、最短报警时长、豁免时段）均在**请求体**中按次提交，无需环境配置。

## 三、接口说明

### `POST /api/v1/cold-storage/temperature/review`

#### 请求体

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `readings` | 数组（≥2 项） | 同一冷库的温度读数，时间必须**严格递增**且**携带时区** |
| `readings[].time` | ISO 8601 datetime | 读数时间，如 `2026-09-25T08:00:00+08:00` |
| `readings[].temperature` | number | 温度（与上限同单位，如 °C） |
| `temperature_limit` | number | 温度上限，温度**严格大于**该值视为越限 |
| `max_sample_interval_seconds` | number（>0） | 最大采样间隔（秒）。相邻读数间隔超过它，该间隔标记为**未知**，不做插值，越限区间不跨越 |
| `min_alarm_duration_seconds` | number（≥0） | 最短报警时长（秒）。扣除豁免后每段持续时长达到该值才计为一次报警 |
| `maintenance_windows` | 数组 | 维护豁免时段，可重叠/相邻（服务端自动合并）；`end` 必须严格晚于 `start`，均须携带时区 |

#### 响应体

| 字段 | 说明 |
| --- | --- |
| `timezone` | 输出时间采用的时区（取自首个读数；内部统一按 UTC 计算） |
| `alarms` | 报警区间：扣除豁免后时长达标的连续段 |
| `excursion_intervals` | 扣除豁免**前**温度高于上限的连续区间（便于追溯） |
| `unknown_intervals` | 采样断档形成的未知区间 |

每个区间含 `start`（含）、`end`（不含）与 `duration_seconds`（秒）。

#### 请求示例

```bash
curl -X POST http://localhost:8000/api/v1/cold-storage/temperature/review \
  -H 'Content-Type: application/json' \
  -d '{
    "readings": [
      {"time": "2026-09-25T08:00:00+08:00", "temperature": -18.0},
      {"time": "2026-09-25T08:05:00+08:00", "temperature": -15.6},
      {"time": "2026-09-25T08:10:00+08:00", "temperature": -14.0},
      {"time": "2026-09-25T08:15:00+08:00", "temperature": -18.2}
    ],
    "temperature_limit": -18.0,
    "max_sample_interval_seconds": 600,
    "min_alarm_duration_seconds": 240,
    "maintenance_windows": [
      {"start": "2026-09-25T08:12:00+08:00", "end": "2026-09-25T08:13:00+08:00"}
    ]
  }'
```

#### 响应示例

```json
{
  "timezone": "+08:00",
  "alarms": [
    {"start": "2026-09-25T08:00:00+08:00", "end": "2026-09-25T08:12:00+08:00", "duration_seconds": 720.0}
  ],
  "excursion_intervals": [
    {"start": "2026-09-25T08:00:00+08:00", "end": "2026-09-25T08:14:45.714286+08:00", "duration_seconds": 885.714286}
  ],
  "unknown_intervals": []
}
```

> 说明：上例中 08:00 温度恰好等于上限，随后的读数 -15.6 已越限，线性插值求得上限交点恰为 08:00（单点不影响时长）；08:10–08:15 由 -14.0 回到 -18.2，上限交点在 08:14:45.714。08:12–08:13 的豁免将连续越限段切成 720 秒与 105.7 秒两段，后者不足 240 秒门槛故不报警。

#### 错误处理（HTTP 422）

以下情况返回 `422 Unprocessable Entity` 并给出中文错误信息：

- 读数时间不严格递增（乱序或时间相等）；
- 时间字段未携带时区（naive datetime）；
- 豁免时段 `end <= start` 等无效时段；
- 读数少于 2 条、采样间隔参数非正、最短报警时长为负、温度非有限数值。

## 四、计算规则

1. **时区归一**：所有输入时间必须带时区，服务端统一换算为 UTC 计算，输出再转回首个读数的时区。
2. **断档判定**：相邻读数时间差 `> max_sample_interval_seconds` 时，整个间隔 `[t_i, t_{i+1})` 标记为未知，不做插值；未知段两侧的越限区间**不会**被合并跨越。
3. **线性插值**：有效间隔内温度视为时间的线性函数；当两端温度分居上限两侧时，按比例精确求解上限交点（`t_cross = t0 + (limit - v0)/(v1 - v0) · (t1 - t0)`）。
4. **越限口径**：温度**严格大于**上限才算越限；恰好等于上限不计时。
5. **区间合并**：相邻有效间隔上的越限子段若首尾相接，合并为一条连续区间。
6. **豁免扣除**：重叠/相邻的豁免窗口先合并，再从每条越限连续区间中扣除，得到若干候选段。
7. **报警筛选**：每个候选段持续时长 `>= min_alarm_duration_seconds` 才输出到 `alarms`。

## 五、项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── analysis.py        # 核心算法（插值、断档、豁免、筛选）
│   ├── schemas.py         # 请求/响应 Pydantic 模型与校验
│   └── main.py            # FastAPI 入口与异常处理
├── tests/
│   ├── test_analysis.py   # 算法单元测试（11 例）
│   └── test_api.py        # HTTP 接口测试（8 例）
├── docker/
│   └── entrypoint.sh      # 一键初始化 + 启动脚本（容器/本地通用）
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt   # 测试额外依赖（pytest、httpx）
└── README.md
```

## 六、本地开发与测试

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q          # 运行全部 19 个测试
uvicorn app.main:app --reload --port 8000
```

## 七、停止服务

```bash
docker compose down        # Compose 方式
# 本地方式：Ctrl+C
```
