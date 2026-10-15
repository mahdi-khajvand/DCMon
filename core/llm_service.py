"""
سرویس LLM با Ollama + Tool Calling برای پروژه DC Monitor
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import ollama

from .llm_tools import OLLAMA_TOOLS, execute_tool

# مدل پیش‌فرض — از env قابل تغییر است
DEFAULT_MODEL = os.environ.get("DC_LLM_MODEL", "qwen2.5:7b")

SYSTEM_PROMPT = """تو «دستیار هوشمند» دیتاسنتر هستی.

منبع حقیقت (فقط این‌ها):
1) فایل دستگاه‌ها (devices): device_id → device_name / model
2) فایل اتصالات (connections): فقط device_id دارد (نه نام). همیشه نام را از devices بگیر.

نقشه ذهنی:
- وقتی کاربر ID می‌دهد (مثل 1004) → با find_device یا get_device_connections همان ID را پیدا کن.
- وقتی نام می‌دهد → اول find_device، بعد اتصالات همان device_id را بگیر.
- در پاسخ نهایی همیشه «نام دستگاه» را بنویس؛ اگر لازم بود ID را داخل پرانتز بگذار.

ابزارها:
- get_device_connections / get_rack_devices / find_device / list_racks / search_all → خواندن
- connect_devices → ساخت لینک
- delete_connection → حذف لینک

قوانین:
1. برای هر سؤال درباره اتصال/رک/دستگاه حتماً ابزار بزن؛ حدس نزن.
2. tool call را در متن پاسخ ننویس.
3. جواب نهایی فقط فارسی، خلاصه و واضح با نام دستگاه‌ها، پورت و نوع لینک.
4. اگر چند نتیجه بود همه را لیست کن.
5. اگر داده نبود صادقانه بگو.
"""


def _clean_answer(text: str) -> str:
    """حذف هرگونه متن tool-call که مدل اشتباهاً در پاسخ نوشته باشد."""
    if not text:
        return ""
    import re
    # بلوک‌های رایج tool call که بعضی مدل‌ها به صورت متن می‌نویسند
    patterns = [
        r"<tool_call>[\s\S]*?</tool_call>",
        r"<\|tool_call\|>[\s\S]*?<\|/tool_call\|>",
        r"```(?:json|tool|xml)?\s*\{[\s\S]*?\"name\"\s*:\s*\"(?:get_device_connections|get_rack_devices|find_device|list_racks|search_all|connect_devices|delete_connection)\"[\s\S]*?\}\s*```",
        r"(?:invoke|call|run)\s+(?:tool\s+)?(?:get_device_connections|get_rack_devices|find_device|list_racks|search_all|connect_devices|delete_connection)\s*\([^)]*\)",
        r"\{[^{}]*\"name\"\s*:\s*\"(?:get_device_connections|get_rack_devices|find_device|list_racks|search_all|connect_devices|delete_connection)\"[^{}]*\}",
        r"tool_call\s*[:=].*",
        r"```tool_code[\s\S]*?```",
    ]
    cleaned = text
    for p in patterns:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE | re.MULTILINE)
    # خطوط خالی تکراری را کم کن
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


class DCLLMService:
    def __init__(self, model: str = None):
        self.model = model or DEFAULT_MODEL
        self._ensure_model()

    def _ensure_model(self):
        try:
            models = ollama.list()
            names = []
            if hasattr(models, "models"):
                names = [getattr(m, "model", None) or getattr(m, "name", "") for m in models.models]
            elif isinstance(models, dict):
                names = [m.get("name") or m.get("model", "") for m in models.get("models", [])]
            if not any(self.model in (n or "") for n in names):
                print(f"[llm] دانلود مدل {self.model} ...")
                ollama.pull(self.model)
            print(f"[llm] مدل {self.model} آماده است")
        except Exception as e:
            print(f"[llm] هشدار بررسی مدل: {e}")

    def chat(
        self,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,
        max_tool_rounds: int = 3,
    ) -> Dict[str, Any]:
        """
        چت با پشتیبانی tool calling.
        history: [{"role":"user"|"assistant","content":"..."}, ...]
        """
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        if history:
            # فقط چند پیام آخر
            for h in history[-12:]:
                if h.get("role") in ("user", "assistant") and h.get("content"):
                    messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_message})

        tools_used = []
        try:
            for _ in range(max_tool_rounds):
                resp = ollama.chat(
                    model=self.model,
                    messages=messages,
                    tools=OLLAMA_TOOLS,
                    options={"temperature": 0.2, "num_predict": 1200},
                )
                msg = resp.get("message") or {}
                tool_calls = msg.get("tool_calls") or []
                content = (msg.get("content") or "").strip()

                if not tool_calls:
                    # اگر مدل tool call را به صورت متن نوشته، سعی کن استخراج و اجرا کنی
                    recovered = self._try_recover_tool_calls(content)
                    if recovered:
                        for name, args in recovered:
                            result = execute_tool(name, args)
                            tools_used.append({"tool": name, "args": args})
                            messages.append({"role": "assistant", "content": content})
                            messages.append({
                                "role": "tool",
                                "content": json.dumps(result, ensure_ascii=False, default=str),
                            })
                        continue  # یک دور دیگر برای تولید پاسخ نهایی

                    answer = _clean_answer(content)
                    return {
                        "answer": answer,
                        "tools_used": tools_used,
                        "model": self.model,
                    }

                # اضافه کردن پیام assistant که tool صدا زده
                messages.append(msg)

                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or ""
                    # arguments ممکن است dict یا رشته JSON باشد
                    raw_args = fn.get("arguments")
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except Exception:
                            args = {}
                    else:
                        args = raw_args or {}

                    result = execute_tool(name, args)
                    tools_used.append({"tool": name, "args": args})

                    messages.append({
                        "role": "tool",
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })

            # اگر بعد از چند دور هنوز tool می‌خواست، پاسخ آخر را بگیر
            final = ollama.chat(
                model=self.model,
                messages=messages,
                options={"temperature": 0.2, "num_predict": 1200},
            )
            answer = _clean_answer((final.get("message") or {}).get("content") or "")
            return {
                "answer": answer,
                "tools_used": tools_used,
                "model": self.model,
            }

        except Exception as e:
            return {
                "answer": f"❌ خطا در ارتباط با مدل: {e}\n\nمطمئن شو Ollama در حال اجراست (`ollama serve`) و مدل {self.model} نصب است.",
                "tools_used": tools_used,
                "model": self.model,
                "error": str(e),
            }

    def _try_recover_tool_calls(self, text: str) -> List[tuple]:
        """اگر مدل tool call را به صورت متن نوشته، استخراج کن."""
        if not text:
            return []
        import re
        found = []
        # الگوی ساده برای get_device_connections با device
        m = re.search(
            r'(?:get_device_connections|get_rack_devices|find_device|search_all|connect_devices|delete_connection)\s*(?:[\(\{]|with\s+)?["\']?(\w+)["\']?\s*(?:[\)\}])?',
            text,
            re.IGNORECASE,
        )
        if m:
            name_guess = m.group(0).split("(")[0].split("{")[0].strip().lower()
            arg = m.group(1)
            if "get_device_connections" in name_guess:
                found.append(("get_device_connections", {"device": arg}))
            elif "get_rack_devices" in name_guess:
                found.append(("get_rack_devices", {"rack": arg}))
            elif "find_device" in name_guess:
                found.append(("find_device", {"query": arg}))
            elif "search_all" in name_guess:
                found.append(("search_all", {"query": arg}))
        # الگوی JSON-like
        for m in re.finditer(
            r'\{\s*"name"\s*:\s*"(get_device_connections|get_rack_devices|find_device|list_racks|search_all|connect_devices|delete_connection)"\s*,\s*"arguments"\s*:\s*(\{[^}]+\})',
            text,
            re.IGNORECASE,
        ):
            try:
                args = json.loads(m.group(2))
                found.append((m.group(1), args))
            except Exception:
                pass
        return found


# singleton ساده
_llm: Optional[DCLLMService] = None


def get_llm() -> DCLLMService:
    global _llm
    if _llm is None:
        _llm = DCLLMService()
    return _llm
