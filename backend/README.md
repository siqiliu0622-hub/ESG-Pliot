# ESG Flask Backend

这个目录为当前 ESG 原型补了一套可运行的 Flask + SQLite 后端。

## 功能

- 自动初始化 SQLite 数据库和示例数据
- 提供监测、合规、供应链、社区劳工四类 API
- 支持社区反馈提交
- 支持导出简单 JSON 报告
- 默认可直接返回根目录下的前端原型页面

## 运行方式

```bash
cd /Users/zhanghang/Desktop/esg/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

启动后访问：

- `http://127.0.0.1:5000/`
- `http://127.0.0.1:5000/api/health`

## API 概览

- `GET /api/health`
- `GET /api/monitoring`
- `GET /api/compliance/standards`
- `POST /api/compliance/generate`
- `GET /api/suppliers`
- `GET /api/social`
- `POST /api/social/feedback`
- `GET /api/report`

## 请求示例

```bash
curl -X POST http://127.0.0.1:5000/api/compliance/generate \
  -H "Content-Type: application/json" \
  -d '{"selected_standards":["T/CHINCA 指引","ISSB 准则"]}'
```
