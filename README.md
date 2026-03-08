# 税悟 AI - 智能财税助手 Demo

> 帮助初创企业/个体工商户老板，将复杂报税流程变成"照猫画虎"的简单操作。

---

## 项目结构

```
税悟AI/
├── frontend/               # 前端页面
│   ├── index.html          # 首页（仪表盘）
│   ├── 申报工具箱.html      # 智能数据采集页
│   └── 辅助报税.html        # 辅助报税导航页
├── backend/                # 后端 API
│   ├── main.py             # FastAPI 主程序
│   ├── requirements.txt    # Python 依赖
│   └── .env.example        # 环境变量示例
└── 项目核心文档/             # 产品文档
```

---

## 核心功能说明

### 页面一：申报工具箱（智能数据采集）
**文件：** `frontend/申报工具箱.html`  
**功能：**
- **对话式输入**：老板用大白话描述财务情况，如"本月开票5万，发工资3万，房租5000"
- **Excel 文件上传**：上传财务报表（利润表/资产负债表），AI 自动提取关键数值
- **数据确认卡片**：AI 提取后展示"收入/成本/利润/从业人数"等核心指标让用户确认
- **跳转辅助报税**：确认后一键进入报表填写引导页

### 页面二：辅助报税（报表导航）
**文件：** `frontend/辅助报税.html`  
**功能：**
- **增值税申报表展示**：模拟真实税务局申报表格式，AI 自动填入计算值（绿色高亮）
- **AI 助手面板（右侧）**：实时解释每个数据的来源和填写逻辑
- **税收优惠提示**：自动识别免税政策（月收入≤10万免征增值税等）
- **下载/确认按钮**：支持下载 PDF 申报草稿

---

## 快速启动

### 方式一：纯前端模式（最简单，不需要后端）

直接用浏览器打开 HTML 文件即可，AI 处理结果使用模拟数据展示：

```bash
# 方法1: 直接双击打开
open frontend/申报工具箱.html

# 方法2: 用 Python 起一个本地服务器
cd frontend
python3 -m http.server 8080
# 然后访问 http://localhost:8080/申报工具箱.html
```

### 方式二：完整前后端模式（接入真实 AI）

**1. 安装 Python 依赖：**
```bash
cd backend
pip3 install -r requirements.txt
```

**2. 配置火山引擎 API Key：**
```bash
cp .env.example .env
# 编辑 .env 填入你的 API Key
```

**3. 启动后端：**
```bash
cd backend
python3 main.py
# 后端运行在 http://localhost:8000
```

**4. 打开前端：**
```bash
cd frontend
python3 -m http.server 8080
# 访问 http://localhost:8080/申报工具箱.html
```

---

## API 接口说明

### POST `/api/extract`
提取财务数据（对话文本 or 文件上传）

**请求体：**
```json
{
  "chat_text": "本月开票收入5万，其中3万是1%税率，2万免税；支出办公费2000元",
  "company_info": {
    "name": "某某科技有限公司",
    "tax_id": "91310115XXXXXXXX",
    "tax_type": "小规模纳税人",
    "employee_count": 5,
    "asset_total": 200
  }
}
```

**返回：**
```json
{
  "success": true,
  "data": {
    "revenue": { "total": 50000, "taxable_1_percent": 30000, "tax_free": 20000 },
    "profit_info": { "total_profit": 27800, "total_cost": 22200 },
    "company_status": { "employee_count": 5, "asset_total_myr": 200 }
  }
}
```

### POST `/api/calculate`
计算税款并生成报表填报数据

**请求体：**（`/api/extract` 返回的 `data` 字段）

**返回：** 增值税申报表、企业所得税申报表、文化事业建设费的各行次数值 JSON

### GET `/api/report/{session_id}`
获取已生成的报表数据

---

## 业务规则说明

### 增值税（小规模纳税人）
- 月销售额 ≤ 10万：**免征增值税**（2026年政策）
- 月销售额 > 10万：按 3% 征收率计税
- 附加税 = 应纳增值税 × (城建税7% + 教育费附加3% + 地方教育附加2%)

### 企业所得税（季度预缴）
- 小型微利企业标准：从业人数 ≤ 300人，资产总额 ≤ 5000万，年利润 ≤ 300万
- 小微企业实际税率：5%（利润额 ≤ 300万部分）
- 一般企业税率：25%

### 文化事业建设费
- 仅适用于广告业、娱乐业
- 费率：3%

---

## 火山引擎 API 接入状态 ✅ 已接入

**模型：** `doubao-seed-2-0-pro-260215`（支持推理+多模态）  
**接口：** `https://ark.cn-beijing.volces.com/api/v3/responses`

AI 已正式接入，财务数据提取使用真实大模型，税务计算使用确定性 Python 引擎（保证100%准确）。

降级机制：若 API 超时或不可用，自动切换 Mock 规则提取，前端无感知。

---

## 免责声明

本产品仅提供报税**参考建议**，最终纳税申报结果由纳税人自行核对确认，税悟 AI 不承担任何法律责任。

---

## 下一步规划

- [x] 接入字节火山引擎 doubao-seed-2-0-pro-260215 ✅
- [x] 三路并行数据池架构（对话+发票+报表同时采集）✅
- [x] 发票 OCR 识别（多模态图片理解）✅
- [x] Excel/CSV 财务报表上传解析 ✅
- [x] 数据池汇总后一键生成申报单 ✅
- [ ] 完善企业所得税 A200000 申报表
- [ ] 添加用户注册/登录（MySQL 持久化存储）
- [ ] PDF 报表文字提取（pdfplumber）
