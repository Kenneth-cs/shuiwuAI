"""
税悟 AI - 后端 API
基于 FastAPI，提供财务数据提取和税务计算接口
AI 引擎：火山引擎 doubao-seed-2-0-pro-260215（Responses API）
降级方案：Mock 规则提取（API 不可用时自动切换）
"""

import os
import re
import json
import time
import logging
from typing import Optional
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# 加载环境变量（从 backend/.env 文件读取，本地开发时使用）
load_dotenv()

# API Key 从环境变量读取，不在代码里硬编码
# 本地开发：在 backend/.env 中设置 VOLCENGINE_API_KEY=你的key
# 参考：backend/.env.example
_DEFAULT_API_KEY = os.getenv("VOLCENGINE_API_KEY", "")

# 日志配置
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="税悟 AI API",
    description="智能财税助手后端接口",
    version="1.0.0",
)

# 跨域配置（允许前端 HTML 调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== 数据模型 =====

class CompanyInfo(BaseModel):
    name: str = "未填写企业名称"
    tax_id: str = ""
    tax_type: str = "small"  # small / general
    period: str = ""
    industry: str = "tech"
    employee_count: int = 0
    asset_total: float = 0.0  # 万元

class ExtractRequest(BaseModel):
    input_type: str = "chat"  # chat / file / invoice
    chat_text: Optional[str] = None
    company_info: Optional[CompanyInfo] = None

class RevenueData(BaseModel):
    total: float = 0.0
    taxable_1_percent: float = 0.0   # 1%税率不含税销售额
    taxable_3_percent: float = 0.0   # 3%税率不含税销售额
    tax_free: float = 0.0             # 免税销售额

class ProfitData(BaseModel):
    total_profit: float = 0.0
    total_cost: float = 0.0

class CompanyStatus(BaseModel):
    employee_count: int = 0
    asset_total_myr: float = 0.0  # 万元
    industry_type: str = "tech"

class FinancialData(BaseModel):
    revenue: RevenueData
    profit_info: ProfitData
    company_status: CompanyStatus
    ai_reasoning: Optional[str] = None

class CalculateRequest(BaseModel):
    revenue: RevenueData
    profit_info: ProfitData
    company_status: CompanyStatus
    company_info: Optional[CompanyInfo] = None


# ===== 工具函数 =====

def round2(n: float) -> float:
    """保留两位小数"""
    return round(n, 2)

def extract_numbers_from_text(text: str) -> list:
    """从文本中提取数字（支持 X万、X.X万 格式）"""
    results = []
    # 匹配金额格式：12万、3.5万、50000、5,000
    patterns = [
        r'(\d+(?:\.\d+)?)万',   # X万
        r'(\d{4,})',             # 4位以上纯数字
    ]
    for p in patterns:
        for m in re.finditer(p, text):
            val = float(m.group(1))
            if '万' in m.group(0):
                val *= 10000
            if 100 < val < 100000000:  # 过滤无意义数字
                results.append(val)
    return results


# ===== 核心：AI 数据提取（Mock + 火山引擎预留接口）=====

async def call_volcengine_llm(prompt: str, system_prompt: str) -> str:
    """
    调用火山引擎 doubao-seed-2-0-pro-260215
    使用 /v3/responses 接口（Responses API 格式）
    返回：LLM 输出的文本内容，失败时返回 None
    """
    api_key = os.getenv("VOLCENGINE_API_KEY", _DEFAULT_API_KEY)
    model = os.getenv("VOLCENGINE_MODEL", "doubao-seed-2-0-pro-260215")
    base_url = os.getenv("VOLCENGINE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

    # 将 system prompt 拼入 user content，因为 Responses API 不支持 system role
    full_prompt = f"{system_prompt}\n\n---\n\n{prompt}"

    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base_url}/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": full_prompt,
                                }
                            ],
                        }
                    ],
                }
            )
            resp.raise_for_status()
            result = resp.json()

            # 从 output 列表中提取 message 类型的文本内容
            # 结构：output[].type == "message" -> content[].type == "output_text" -> text
            for item in result.get("output", []):
                if item.get("type") == "message":
                    for part in item.get("content", []):
                        if part.get("type") == "output_text":
                            text = part.get("text", "")
                            logger.info(f"[volcengine] 返回 tokens={result.get('usage', {}).get('total_tokens')}")
                            return text

            logger.warning("[volcengine] 响应中未找到 output_text")
            return None

    except Exception as e:
        logger.error(f"[volcengine] API 调用失败: {e}")
        return None


def extract_json_from_text(text: str) -> Optional[dict]:
    """
    从 LLM 返回的文本中提取 JSON
    处理 LLM 可能包裹 ```json ... ``` 的情况
    """
    if not text:
        return None
    # 去掉 markdown 代码块
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()
    # 找到第一个 { 到最后一个 }
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end+1])
    except json.JSONDecodeError:
        return None


# 财务数据提取的 System Prompt（符合 PRD 设计）
EXTRACT_SYSTEM_PROMPT = """
# Role
你是一个资深的中国税务会计助手，精通广东省小规模纳税人报税规则及《小企业会计准则》。

# Task
请从用户提供的【原始输入】（聊天记录或财务描述）中，提取关键的财务指标。

# 单位要求（非常重要）
- 所有金额字段的单位必须是【元（人民币）】，不是万元
- 如果用户说"5万"，应填入 50000.0（不是 5.0）
- 如果用户说"3000元"，应填入 3000.0
- 资产总额 asset_total_myr 单位是【万元】，如"200万"填入 200.0

# Response Format (JSON)
必须只输出纯 JSON，不要有任何其他文字：
{
  "revenue": {
    "total": 0.0,
    "taxable_1_percent": 0.0,
    "taxable_3_percent": 0.0,
    "tax_free": 0.0
  },
  "profit_info": {
    "total_profit": 0.0,
    "total_cost": 0.0
  },
  "company_status": {
    "employee_count": 0,
    "asset_total_myr": 0.0,
    "industry_type": "tech"
  },
  "ai_reasoning": "一句话说明提取逻辑"
}

# 字段说明
- revenue.total：含税总收入（元），用于判断月销售额是否超过10万免税额度
- revenue.taxable_1_percent：1%征收率的不含税销售额（元），计算方式：含税金额 ÷ 1.01
- revenue.taxable_3_percent：3%征收率的不含税销售额（元），计算方式：含税金额 ÷ 1.03
- revenue.tax_free：免税销售额（元），直接取用户描述的金额
- profit_info.total_cost：总成本费用（工资+房租+其他支出之和，元）
- profit_info.total_profit：利润总额（元），= total - total_cost（注意用不含税口径估算）
- company_status.asset_total_myr：资产总额（万元）
- industry_type 从以下选择: tech / trade / ad / entertainment / other

# 其他规则
1. 若信息缺失则设为 0
2. 若用户提到"开票收入"或"含税收入"，需除以对应税率系数换算为不含税金额
3. 如果用户说"2万免税"，revenue.tax_free = 20000.0，不要换算
"""


def mock_extract_data(text: str, company_info: CompanyInfo) -> dict:
    """
    Mock AI 提取（无 API Key 时使用）
    基于简单规则从文本提取数字
    """
    nums = extract_numbers_from_text(text or "")

    # 尝试智能推断
    revenue_total = 0.0
    taxable_1 = 0.0
    taxable_3 = 0.0
    tax_free = 0.0
    cost_total = 0.0

    # 关键词匹配
    # 收入识别
    income_match = re.search(r'(?:开票|收入|销售).{0,5}?(\d+(?:\.\d+)?)\s*万?元', text or "")
    if income_match:
        v = float(income_match.group(1))
        revenue_total = v * 10000 if '万' in text[max(0, income_match.start()-2):income_match.end()+2] else v
    elif nums:
        revenue_total = nums[0]

    # 1%税率识别
    pct1_match = re.search(r'(\d+(?:\.\d+)?)\s*万?元.*?1%', text or "")
    if pct1_match:
        v = float(pct1_match.group(1))
        pct1_incl = v * 10000 if v < 1000 else v
        taxable_1 = round2(pct1_incl / 1.01)
    else:
        taxable_1 = round2(revenue_total * 0.6 / 1.01)

    # 免税收入
    free_match = re.search(r'(\d+(?:\.\d+)?)\s*万?元.*?免税', text or "")
    if free_match:
        v = float(free_match.group(1))
        tax_free = v * 10000 if v < 1000 else v
    else:
        tax_free = round2(revenue_total - taxable_1 * 1.01 - taxable_3 * 1.03)
        tax_free = max(0, tax_free)

    # 支出/成本识别
    cost_keywords = ['工资', '薪酬', '房租', '租金', '办公', '支出', '成本', '费用']
    cost_nums = []
    for kw in cost_keywords:
        m = re.search(rf'{kw}.{{0,10}}?(\d+(?:\.\d+)?)\s*万?元', text or "")
        if m:
            v = float(m.group(1))
            cost_nums.append(v * 10000 if v < 1000 else v)
    cost_total = sum(cost_nums) if cost_nums else round2(revenue_total * 0.44)

    profit = round2(revenue_total - cost_total)

    reasoning_lines = [
        f"• 识别到含税总收入约 {revenue_total:,.2f} 元",
        f"• 1%税率不含税收入约 {taxable_1:,.2f} 元",
        f"• 免税收入约 {tax_free:,.2f} 元",
        f"• 总成本费用约 {cost_total:,.2f} 元",
        f"• 估算利润总额约 {profit:,.2f} 元",
        "如有偏差，请直接修改上方数值后确认。",
    ]

    return {
        "revenue": {
            "total": round2(revenue_total),
            "taxable_1_percent": taxable_1,
            "taxable_3_percent": round2(taxable_3),
            "tax_free": round2(tax_free),
        },
        "profit_info": {
            "total_profit": profit,
            "total_cost": round2(cost_total),
        },
        "company_status": {
            "employee_count": company_info.employee_count or 5,
            "asset_total_myr": company_info.asset_total or 200.0,
            "industry_type": company_info.industry or "tech",
        },
        "ai_reasoning": "\n".join(reasoning_lines),
    }


# ===== 税务计算引擎（确定性逻辑，不依赖 AI）=====

def calculate_taxes(data: CalculateRequest) -> dict:
    """
    核心税务计算引擎
    税额计算由 Python 代码完成，AI 只负责数据提取和分类
    """
    rev = data.revenue
    profit = data.profit_info
    status = data.company_status
    info = data.company_info or CompanyInfo()

    total_taxable = round2(rev.taxable_1_percent + rev.taxable_3_percent)
    total_sales = round2(total_taxable + rev.tax_free)

    # ---- 增值税计算 ----
    is_vat_exempt = total_sales <= 100000  # 月销售额≤10万免征
    vat_1pct = round2(rev.taxable_1_percent * 0.01)
    vat_3pct = round2(rev.taxable_3_percent * 0.03)
    vat_due = 0.0 if is_vat_exempt else round2(vat_1pct + vat_3pct)

    # 附加税
    urban_build = round2(vat_due * 0.07)   # 城建税 7%（城区）
    edu_surcharge = round2(vat_due * 0.03) # 教育费附加 3%
    local_edu = round2(vat_due * 0.02)     # 地方教育附加 2%
    surcharge_total = round2(urban_build + edu_surcharge + local_edu)

    # ---- 企业所得税 ----
    real_profit = profit.total_profit
    is_small_micro = (
        status.employee_count <= 300 and
        status.asset_total_myr <= 5000 and
        real_profit <= 3000000
    )
    cit_rate = 0.05 if is_small_micro else 0.25
    cit_due = round2(real_profit * cit_rate) if real_profit > 0 else 0.0
    cit_reduction = round2(real_profit * (0.25 - cit_rate)) if is_small_micro and real_profit > 0 else 0.0

    # ---- 文化事业建设费 ----
    is_ad_or_entertainment = status.industry_type in ("ad", "entertainment")
    culture_due = round2(total_taxable * 0.03) if is_ad_or_entertainment else 0.0

    # ---- 组装报表数据 ----
    period = info.period or time.strftime("%Y-%m")

    # 增值税申报表行次
    vat_report = {
        "report_name": "增值税及附加税费申报表（小规模纳税人适用）",
        "period": period,
        "is_vat_exempt": is_vat_exempt,
        "mappings": [
            {"row_id": "1",  "name": "3%税率不含税销售额",     "value": rev.taxable_3_percent, "guide": "请在电子税务局第1行填入此数值"},
            {"row_id": "3",  "name": "1%税率(普票)不含税销售额","value": rev.taxable_1_percent, "guide": "请在电子税务局第3行填入此数值"},
            {"row_id": "10", "name": "合计（应税销售额）",       "value": total_taxable,         "guide": "第1行+第4行+第5行之和"},
            {"row_id": "11", "name": "免税销售额",               "value": rev.tax_free,          "guide": "免税收入填此行"},
            {"row_id": "20", "name": f"本期应纳增值税{'（免征）' if is_vat_exempt else ''}",  "value": vat_due, "guide": "应税销售额×税率"},
            {"row_id": "30", "name": "本期应纳税额合计",         "value": vat_due,               "guide": "即本期实际缴纳增值税额"},
            {"row_id": "31", "name": "城市维护建设税",            "value": urban_build,           "guide": "增值税×7%"},
            {"row_id": "32", "name": "教育费附加",                "value": edu_surcharge,         "guide": "增值税×3%"},
            {"row_id": "33", "name": "地方教育附加",              "value": local_edu,             "guide": "增值税×2%"},
            {"row_id": "34", "name": "附加税费合计",              "value": surcharge_total,       "guide": "城建税+教育费附加+地方教育附加"},
        ],
    }

    # 企业所得税申报表行次
    cit_report = {
        "report_name": "居民企业所得税月（季）度预缴纳税申报表（A类）",
        "is_small_micro": is_small_micro,
        "cit_rate": cit_rate,
        "mappings": [
            {"row_id": "1",  "name": "营业收入",        "value": rev.total,        "guide": "本期含税收入总额"},
            {"row_id": "2",  "name": "营业成本",        "value": profit.total_cost, "guide": "总收入-利润"},
            {"row_id": "10", "name": "实际利润额",      "value": real_profit,       "guide": "利润表利润总额"},
            {"row_id": "15", "name": "应纳税所得额",    "value": max(0, real_profit), "guide": "实际利润额"},
            {"row_id": "16", "name": f"税率（{cit_rate*100:.0f}%）", "value": cit_rate * 100, "guide": f"{'小微5%' if is_small_micro else '一般25%'}"},
            {"row_id": "17", "name": "应纳所得税额",    "value": cit_due,           "guide": "应纳税所得额×税率"},
            {"row_id": "18", "name": "减免所得税额",    "value": cit_reduction,     "guide": "小型微利减免"},
            {"row_id": "20", "name": "本期应补税额",    "value": cit_due,           "guide": "应纳-减免-已预缴"},
        ],
    }

    # 文化事业建设费
    culture_report = {
        "report_name": "文化事业建设费申报表",
        "applicable": is_ad_or_entertainment,
        "mappings": [
            {"row_id": "1", "name": "计费销售额", "value": total_taxable if is_ad_or_entertainment else 0, "guide": "广告/娱乐服务不含税收入"},
            {"row_id": "2", "name": "费率", "value": 3, "guide": "广告业和娱乐业费率3%"},
            {"row_id": "3", "name": "应缴文化事业建设费", "value": culture_due, "guide": "计费销售额×3%"},
        ],
    }

    return {
        "vat": vat_report,
        "cit": cit_report,
        "culture": culture_report,
        "summary": {
            "vat_due": vat_due,
            "surcharge_total": surcharge_total,
            "cit_due": cit_due,
            "culture_due": culture_due,
            "total_tax": round2(vat_due + surcharge_total + cit_due + culture_due),
            "is_vat_exempt": is_vat_exempt,
            "is_small_micro": is_small_micro,
        }
    }


# ===== API 路由 =====

@app.get("/")
async def root():
    """健康检查"""
    api_key = os.getenv("VOLCENGINE_API_KEY", _DEFAULT_API_KEY)
    return {
        "status": "ok",
        "service": "税悟 AI API",
        "version": "1.0.0",
        "model": os.getenv("VOLCENGINE_MODEL", "doubao-seed-2-0-pro-260215"),
        "ai_enabled": bool(api_key and api_key != "your_volcengine_api_key_here"),
    }


@app.post("/api/extract")
async def extract_financial_data(req: ExtractRequest):
    """
    接口1：财务数据提取
    - 输入：对话文本 / 文件（后续支持）
    - 输出：结构化财务指标 JSON
    - AI 逻辑：优先调用火山引擎 Doubao-pro，不可用时降级为 Mock
    """
    logger.info(f"[extract] input_type={req.input_type}, text_len={len(req.chat_text or '')}")

    company_info = req.company_info or CompanyInfo()

    # 构建 prompt
    user_prompt = f"""
企业信息：
- 名称：{company_info.name}
- 纳税人类型：{'小规模纳税人' if company_info.tax_type == 'small' else '一般纳税人'}
- 行业：{company_info.industry}
- 从业人数：{company_info.employee_count} 人
- 资产总额：{company_info.asset_total} 万元

原始输入：
{req.chat_text or '无文本输入'}
"""

    # 尝试调用火山引擎 LLM
    llm_result = await call_volcengine_llm(user_prompt, EXTRACT_SYSTEM_PROMPT)

    if llm_result:
        data = extract_json_from_text(llm_result)
        if data:
            # 补充公司信息（LLM 可能未能从文本中提取到人数/资产）
            if "company_status" in data:
                cs = data["company_status"]
                if not cs.get("employee_count"):
                    cs["employee_count"] = company_info.employee_count
                if not cs.get("asset_total_myr"):
                    cs["asset_total_myr"] = company_info.asset_total
                if not cs.get("industry_type"):
                    cs["industry_type"] = company_info.industry

            # 单位自动纠正：如果 revenue.total < 10000 且文本含"万"，则 × 10000
            rev = data.get("revenue", {})
            text_has_wan = '万' in (req.chat_text or '')
            if text_has_wan and rev.get("total", 0) < 10000 and rev.get("total", 0) > 0:
                scale = 10000
                logger.info(f"[extract] 检测到单位为万元，自动 ×{scale} 换算")
                for key in ["total", "taxable_1_percent", "taxable_3_percent", "tax_free"]:
                    if key in rev:
                        rev[key] = round(rev[key] * scale, 2)
                pf = data.get("profit_info", {})
                for key in ["total_profit", "total_cost"]:
                    if key in pf:
                        pf[key] = round(pf[key] * scale, 2)

            logger.info(f"[extract] 火山引擎 LLM 提取成功, revenue.total={data.get('revenue',{}).get('total')}")
            return {"success": True, "data": data, "source": "volcengine"}
        else:
            logger.warning(f"[extract] LLM 返回内容无法解析为 JSON，降级 Mock\n内容片段: {llm_result[:200]}")

    # 降级：Mock 数据提取
    data = mock_extract_data(req.chat_text, company_info)
    logger.info("[extract] 使用 Mock 数据提取")
    return {"success": True, "data": data, "source": "mock"}


@app.post("/api/calculate")
async def calculate_tax_report(req: CalculateRequest):
    """
    接口2：税务计算
    - 输入：结构化财务数据
    - 输出：各申报表的行次数值（纯 Python 确定性计算，不依赖 AI）
    """
    logger.info(f"[calculate] revenue_total={req.revenue.total}, profit={req.profit_info.total_profit}")
    result = calculate_taxes(req)
    return {"success": True, "data": result}


@app.post("/api/process")
async def full_process(req: ExtractRequest):
    """
    接口3：一站式处理（提取 + 计算）
    前端调用此接口可一次获得完整报表数据
    """
    # Step 1: 提取
    extract_resp = await extract_financial_data(req)
    if not extract_resp["success"]:
        raise HTTPException(status_code=500, detail="数据提取失败")

    extracted = extract_resp["data"]

    # Step 2: 计算
    calc_req = CalculateRequest(
        revenue=RevenueData(**extracted["revenue"]),
        profit_info=ProfitData(**extracted["profit_info"]),
        company_status=CompanyStatus(**extracted["company_status"]),
        company_info=req.company_info,
    )
    tax_result = calculate_taxes(calc_req)

    return {
        "success": True,
        "extracted": extracted,
        "tax_report": tax_result,
        "source": extract_resp.get("source"),
    }


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    接口4：财务报表文件上传
    - 先尝试提取文件可读文本内容（CSV / 简单Excel文本）
    - 将内容传给 LLM 解析财务数据
    - 不可解析时降级返回 Mock
    """
    content = await file.read()
    file_size = len(content)
    fname = file.filename or ""
    logger.info(f"[upload] filename={fname}, size={file_size}")

    text_content = ""

    # 尝试提取文本内容
    if fname.lower().endswith(".csv"):
        try:
            text_content = content.decode("utf-8", errors="ignore")
        except Exception:
            pass
    elif fname.lower().endswith((".xlsx", ".xls")):
        try:
            import openpyxl, io
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets[:3]:  # 只读前3个Sheet
                lines.append(f"[Sheet: {ws.title}]")
                for row in ws.iter_rows(max_row=60, values_only=True):
                    row_vals = [str(v) if v is not None else "" for v in row]
                    line = "\t".join(row_vals).strip()
                    if line.replace("\t", ""):
                        lines.append(line)
            text_content = "\n".join(lines[:200])  # 最多200行
        except ImportError:
            logger.warning("[upload] openpyxl 未安装，无法解析 Excel")
        except Exception as e:
            logger.warning(f"[upload] Excel 解析失败: {e}")
    elif fname.lower().endswith(".pdf"):
        # PDF 暂不解析文本，用 Mock
        logger.info("[upload] PDF 文件暂用 Mock 数据")

    # 有文本内容时，调用 AI 提取
    if text_content.strip():
        prompt = f"""以下是从财务报表文件《{fname}》中提取的表格内容，请分析并提取财税关键指标：

{text_content[:3000]}"""
        llm_result = await call_volcengine_llm(prompt, EXTRACT_SYSTEM_PROMPT)
        if llm_result:
            data = extract_json_from_text(llm_result)
            if data:
                # 单位纠正
                rev = data.get("revenue", {})
                if rev.get("total", 0) < 10000 and rev.get("total", 0) > 0 and '万' in text_content:
                    for key in ["total", "taxable_1_percent", "taxable_3_percent", "tax_free"]:
                        if key in rev: rev[key] = round(rev[key] * 10000, 2)
                    pf = data.get("profit_info", {})
                    for key in ["total_profit", "total_cost"]:
                        if key in pf: pf[key] = round(pf[key] * 10000, 2)

                data.setdefault("ai_reasoning", f"已从《{fname}》中提取财税数据。")
                logger.info(f"[upload] 文件解析成功 via LLM, revenue={data.get('revenue',{}).get('total')}")
                return {"success": True, "data": data, "source": "volcengine"}

    # 降级：Mock 数据
    logger.info(f"[upload] 使用 Mock 数据（文件: {fname}）")
    mock_data = {
        "revenue": {"total": 50000.0, "taxable_1_percent": 29702.97, "taxable_3_percent": 0.0, "tax_free": 20000.0},
        "profit_info": {"total_profit": 27800.0, "total_cost": 22200.0},
        "company_status": {"employee_count": 5, "asset_total_myr": 200.0, "industry_type": "tech"},
        "ai_reasoning": f"已接收文件《{fname}》（{file_size//1024} KB）。\n{'该文件格式暂无法自动解析文本，' if not text_content else ''}已返回演示数据，请根据实际数值修改。",
    }
    return {"success": True, "data": mock_data, "source": "mock"}


# ===== 发票 OCR 数据模型 =====
class InvoiceOCRRequest(BaseModel):
    image_base64: str           # base64 编码的图片
    mime_type: str = "image/jpeg"  # 图片 MIME 类型


# 发票 OCR 的 System Prompt
INVOICE_OCR_PROMPT = """
# Role
你是一个专业的中国增值税发票（含电子发票）识别专家，熟悉国家税务总局全面数字化的电子发票格式。

# Task
仔细识别图片中的发票，提取所有关键字段。图片可能是：电子普通发票、电子专用发票、纸质发票的扫描件。

# Response Format
只输出纯 JSON，不要 markdown，不要解释：
{
  "invoice_type": "电子发票（普通发票）",
  "invoice_no": "26442000001894032886",
  "invoice_date": "2026-02-24",
  "seller_name": "销售方公司名称",
  "seller_tax_id": "销售方统一社会信用代码",
  "buyer_name": "购买方公司名称",
  "buyer_tax_id": "购买方统一社会信用代码",
  "goods_name": "住宿服务",
  "amount_without_tax": 1485.15,
  "tax_rate": "1%",
  "tax_amount": 14.85,
  "amount_total": 1500.00,
  "category": "expense",
  "notes": ""
}

# 字段说明
- invoice_type: 发票种类，如"电子发票（普通发票）"、"增值税专用发票"
- invoice_no: 发票号码（右上角数字）
- invoice_date: 开票日期，格式 YYYY-MM-DD
- seller_name: 销售方名称（右侧销售方信息框）
- buyer_name: 购买方名称（左侧购买方信息框）
- goods_name: 货物或服务名称（项目名称列），如有多项用逗号分隔
- amount_without_tax: 合计行的不含税金额（元），即"金额"列合计
- tax_rate: 税率，如 "1%"、"3%"、"6%"、"0%（免税）"，多税率用主要税率
- tax_amount: 税额合计（元）
- amount_total: 价税合计小写金额（元），即括号内 ¥xxx 的数值
- category: 固定填 "expense"（该企业是购买方，这是进项/支出发票）或 "income"（该企业是销售方，这是销项/收入发票）。通常上传发票的人是购买方，填 "expense"
- notes: 识别说明，若图片不是发票则在此说明

# 重要规则
1. amount_total 从"价税合计（小写）"字段读取，单位是元
2. 若是电子发票图片，发票号码在右上角，格式为20位数字
3. 金额全部用数字（浮点数），不要带"¥"或"元"
4. 若图片确实不是发票，所有金额字段填 null，category 填 null，在 notes 说明
"""


@app.post("/api/ocr-invoice")
async def ocr_invoice(req: InvoiceOCRRequest):
    """
    接口5：发票 OCR 识别
    使用火山引擎 doubao-seed-2-0-pro 多模态能力识别发票图片
    """
    logger.info(f"[ocr-invoice] mime_type={req.mime_type}, base64_len={len(req.image_base64)}")

    if not req.image_base64 or len(req.image_base64) < 100:
        return {"success": False, "message": "图片数据为空，请重新上传"}

    api_key = os.getenv("VOLCENGINE_API_KEY", _DEFAULT_API_KEY)
    model = os.getenv("VOLCENGINE_MODEL", "doubao-seed-2-0-pro-260215")
    base_url = os.getenv("VOLCENGINE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

    # 构造多模态请求（图片 + 文字）
    try:
        import httpx
        # 构造 data URL
        data_url = f"data:{req.mime_type};base64,{req.image_base64}"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base_url}/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_image",
                                    "image_url": data_url,   # base64 data URL
                                },
                                {
                                    "type": "input_text",
                                    "text": INVOICE_OCR_PROMPT,
                                }
                            ],
                        }
                    ],
                }
            )
            resp.raise_for_status()
            result = resp.json()

            # 提取文本输出
            llm_text = None
            for item in result.get("output", []):
                if item.get("type") == "message":
                    for part in item.get("content", []):
                        if part.get("type") == "output_text":
                            llm_text = part.get("text", "")

            if not llm_text:
                raise ValueError("模型未返回有效内容")

            # 解析 JSON
            data = extract_json_from_text(llm_text)
            if not data:
                logger.warning(f"[ocr-invoice] 无法解析 JSON，原始: {llm_text[:300]}")
                # 如果模型说这不是发票，也返回友好提示
                return {"success": False, "message": f"图片识别失败，请确认是否为发票图片。模型回复：{llm_text[:100]}"}

            logger.info(f"[ocr-invoice] 识别成功: {data.get('invoice_type')} ¥{data.get('amount_total')}")
            return {"success": True, "data": data, "source": "volcengine"}

    except Exception as e:
        logger.error(f"[ocr-invoice] 调用失败: {e}")
        # 降级：返回 Mock 发票数据
        mock_invoice = {
            "invoice_type": "增值税普通发票（演示）",
            "invoice_no": "DEMO-001",
            "invoice_date": time.strftime("%Y-%m-%d"),
            "seller_name": "某某供应商",
            "buyer_name": "您的公司",
            "amount_without_tax": 2970.30,
            "tax_rate": "1%",
            "tax_amount": 29.70,
            "amount_total": 3000.00,
            "goods_name": "技术服务费",
            "category": "expense",
            "notes": "API 调用失败，返回演示数据",
        }
        return {"success": True, "data": mock_invoice, "source": "mock"}


# ===== 启动 =====
if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("税悟 AI 后端服务启动")
    print("接口文档：http://localhost:8000/docs")
    print("健康检查：http://localhost:8000/")
    print("=" * 50)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
